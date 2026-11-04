"""FullCare's people outside the home in the mail app: GP practices, specialists, pharmacy, hospital, patient
transport, health authority, facilities and the residents' relatives. They reply by fixed rules (no model at run
time); the variant of a reply is picked by a hash of the mail, so the same actions always give the same text.

Workforce's own family contacts are replaced by the relatives of the care record, so every relative in the address
book belongs to a resident in the record. All addresses use the reserved `.example` domain.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from opsdesk.apps.ids import next_monotonic_id
from opsdesk.fullcare.models import CareState
from opsdesk.workforce.state import Contact, MailMessage, WorkforceState

PREFIX = "care-"  # contact ids the Workforce colleagues leave alone


def _slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).casefold()).strip("-")


def organisations(care: CareState, town: str, de: bool) -> list[Contact]:
    s = _slug(town)
    rows = [
        ("pharmacy", "Apotheke am Markt" if de else "Market Square Pharmacy", "Team Heimversorgung" if de else "Care home team",
         "Apotheke" if de else "Pharmacy", f"heimversorgung@apotheke-{s}.example"),
        ("hospital", f"Kreisklinikum {town}" if de else f"{town} General Hospital", "Patientenaufnahme" if de else "Admissions",
         "Krankenhaus" if de else "Hospital", f"aufnahme@klinikum-{s}.example"),
        ("transport", f"Krankentransport {town}" if de else f"{town} Patient Transport", "Disposition" if de else "Bookings",
         "Krankentransport" if de else "Patient transport", f"dispo@krankentransport-{s}.example"),
        ("authority", f"Gesundheitsamt {town}" if de else f"{town} Health Protection Team", "Infektionsschutz" if de else "Duty officer",
         "Gesundheitsamt" if de else "Health authority", f"infektionsschutz@gesundheitsamt-{s}.example"),
        ("lab", f"Labor am Klinikum {town}" if de else f"{town} Pathology Laboratory", "Befundversand" if de else "Results office",
         "Labor" if de else "Laboratory", f"befunde@labor-{s}.example"),
        ("facilities", "Haustechnik" if de else "Facilities", "Haustechnik" if de else "Facilities team",
         "Haustechnik und Hilfsmittel" if de else "Facilities and equipment", f"haustechnik@heim-{s}.example"),
    ]
    out = [Contact(contact_id=f"{PREFIX}{key}", name=person, organisation=org, role=role, address=addr)
           for key, org, person, role, addr in rows]
    for p in care.physicians:
        out.append(Contact(contact_id=f"{PREFIX}{p.physician_id}", name=p.name, organisation=p.practice,
                           role=("Hausarztpraxis" if de else "GP practice") if p.kind == "gp" else p.specialty, address=p.mail))
    return out


def relatives(care: CareState, de: bool) -> list[Contact]:
    out = []
    for c in care.contacts:
        r = care.resident(c.resident_id)
        if not c.mail or r is None:
            continue
        of = (f"{c.relation} von {'Frau' if r.sex == 'f' else 'Herrn'} {r.family_name}" if de else
              f"{c.relation} of {'Mrs' if r.sex == 'f' else 'Mr'} {r.family_name}")
        out.append(Contact(contact_id=f"{PREFIX}{c.contact_id}", name=c.name, organisation="Angehörige/r" if de else "Relative",
                           role=of, address=c.mail))
    return out


def prepare_app(care: CareState, app: WorkforceState, town: str) -> None:
    """Address book and mailbox for the care workspace (idempotent)."""
    de = app.locale == "de-DE"
    family = {c.contact_id for c in app.contacts if c.contact_id.startswith("contact-family")}
    addresses = {c.address for c in app.contacts if c.contact_id in family}
    app.contacts = [c for c in app.contacts if c.contact_id not in family and not c.contact_id.startswith(PREFIX)]
    app.mails = [m for m in app.mails if m.sender not in addresses and not set(m.to) & addresses]
    app.contacts += organisations(care, town, de) + relatives(care, de)
    if not app.home.user_title.startswith(("Pflegefachkraft", "Registered nurse")):
        app.home.user_title = ("Pflegefachkraft, Assistenz der Pflegedienstleitung" if de else
                               "Registered nurse, assistant to the head of care")


# ---------------------------------------------------------------- replies

def _pick(key: str, options: list[str]) -> str:
    return options[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(options)]


def _residents_named(care: CareState, text: str):
    low = text.casefold()
    return [r for r in care.residents if r.family_name.casefold() in low and
            (r.given_name.casefold() in low or re.search(r"\b(frau|herr|herrn|mrs|mr|ms)\.?\s+" + re.escape(r.family_name.casefold()), low))]


VALUE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:/\s*\d+\s*)?(?:mmhg|mg/dl|mmol/l|°c|%|/min|kg|bpm)", re.IGNORECASE)
TIME = re.compile(r"\b([01]\d|2[0-3])[:.][0-5]\d\b")
DATE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.(?:\d{2,4})?|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
MODE = re.compile(r"sitzend|liegend|rollstuhl|tragestuhl|sitting|lying|stretcher|wheelchair", re.I)


def reply_body(care: CareState, contact: Contact, sent: MailMessage, de: bool, me: str) -> str | None:
    text = f"{sent.subject}\n{sent.body}"
    low = text.casefold()
    named = _residents_named(care, text)
    key = f"{contact.contact_id}:{sent.mail_id}"
    hello = "Guten Tag," if de else "Hello,"
    kind = contact.contact_id[len(PREFIX):]
    doc = care.physician(kind) if kind.startswith("doc-") else None
    if doc is not None:
        if not named:
            return (f"{hello}\n\num welche Bewohnerin oder welchen Bewohner geht es? Bitte mit Namen und Geburtsdatum." if de else
                    f"{hello}\n\nWhich resident is this about? Please give the name and date of birth.")
        if doc.kind == "gp" and all(r.gp_id != doc.physician_id for r in named):
            other = care.physician(named[0].gp_id)
            return (f"{hello}\n\n{named[0].name} ist nicht in unserer Praxis. Zuständig ist {other.name if other else 'die Hausarztpraxis'}." if de else
                    f"{hello}\n\n{named[0].name} is not registered with us. Please contact {other.name if other else 'their GP'}.")
        if not VALUE.search(text):
            return (f"{hello}\n\nwelche Werte haben Sie gemessen, und wann? Bitte mit Einheit." if de else
                    f"{hello}\n\nWhich values did you measure, and when? Please include the units.")
        return _pick(key, [
            f"{hello}\n\ndanke für die Werte. Bitte nach Hausstandard nachmessen und mir das Ergebnis schicken. "
            f"Ich schaue bei der nächsten Visite nach {named[0].name}." if de else
            f"{hello}\n\nThank you for the values. Please repeat the measurement as per your home's standard and send me "
            f"the result. I will see {named[0].name} on my next round.",
            f"{hello}\n\nist angekommen. Bitte weiter beobachten und erneut messen; bei weiterer Verschlechterung rufen Sie "
            f"bitte die Praxis an." if de else
            f"{hello}\n\nReceived. Please keep observing and measure again; if things get worse, please ring the surgery.",
        ]) + f"\n\n{doc.name}"
    if kind == "pharmacy":
        drugs = {m.drug.casefold() for r in named for m in care.medication if m.resident_id == r.resident_id}
        ordered = any(o.kind == "medication" and o.resident_id in {r.resident_id for r in named} for o in care.orders)
        if named and (any(d in low for d in drugs) or ordered):
            return _pick(key, [
                "Guten Tag,\n\nvielen Dank, wir stellen die Medikation um und liefern mit der nächsten Tour." if de else
                "Hello,\n\nThank you – we will change the medication and deliver with the next round.",
                "Guten Tag,\n\nist notiert. Das Rezept holen wir in der Praxis ab, Lieferung morgen früh." if de else
                "Hello,\n\nNoted. We will collect the prescription from the surgery and deliver tomorrow morning."])
        return ("Guten Tag,\n\nfür welche Bewohnerin / welchen Bewohner und welches Medikament? Ohne ärztliche Verordnung "
                "können wir nichts ändern." if de else
                "Hello,\n\nFor which resident and which medicine? We cannot change anything without the prescriber's instruction.")
    if kind == "transport":
        missing = []
        if not named:
            missing.append("Name" if de else "name")
        if not DATE.search(text) and not re.search(r"\b(heute|morgen|today|tomorrow)\b", low):
            missing.append("Datum" if de else "date")
        if not TIME.search(text):
            missing.append("Abholzeit" if de else "pick-up time")
        if not MODE.search(text):
            missing.append("Transportart (sitzend, liegend, Rollstuhl)" if de else "how they travel (sitting, wheelchair, stretcher)")
        if missing:
            return ("Guten Tag,\n\nfür die Buchung fehlt uns noch: " + ", ".join(missing) + "." if de else
                    "Hello,\n\nTo book this we still need: " + ", ".join(missing) + ".")
        when = TIME.search(text).group(0).replace(".", ":")
        return (f"Guten Tag,\n\nder Transport für {named[0].name} ist gebucht, Abholung {when} Uhr am Empfang." if de else
                f"Hello,\n\nTransport for {named[0].name} is booked, pick-up {when} at reception.")
    if kind == "facilities":
        if re.search(r"matratze|mattress", low):
            return ("Hallo,\n\ndie Wechseldruckmatratze bringen wir heute Nachmittag aufs Zimmer." if de else
                    "Hello,\n\nWe will bring the pressure-relief mattress to the room this afternoon.")
        return "Hallo,\n\nist notiert." if de else "Hello,\n\nNoted."
    if kind in ("hospital", "authority", "lab"):
        return ("Guten Tag,\n\nvielen Dank, Ihre Nachricht ist eingegangen." if de else "Hello,\n\nThank you, your message has been received.")
    if kind.startswith("contact-"):  # a relative
        return _pick(key, ["Guten Tag,\n\nvielen Dank für die Nachricht." if de else "Hello,\n\nThank you for letting me know.",
                           "Guten Tag,\n\ndanke, dass Sie Bescheid geben." if de else "Hello,\n\nThanks for the update."])
    return None


def after_mail(ws, data: dict[str, Any]) -> None:
    app, care = ws.app, ws.care
    sent = next((m for m in app.mails if m.mail_id == data.get("mail_id")), None)
    if sent is None:
        return
    de = app.locale == "de-DE"
    me = app.home.user_name.split(" ")[0]
    for address in sent.to:
        contact = next((c for c in app.contacts if c.address.casefold() == address.casefold()
                        and c.contact_id.startswith(PREFIX)), None)
        if contact is None:
            continue
        body = reply_body(care, contact, sent, de, me)
        if body is None:
            continue
        if not body.rstrip().endswith(contact.name) and not contact.contact_id.startswith(f"{PREFIX}doc-"):
            body += f"\n\n{contact.name}\n{contact.organisation}"
        app.mails.append(MailMessage(mail_id=next_monotonic_id("mail", [m.mail_id for m in app.mails]), folder="inbox",
                                     sender=contact.address, sender_name=contact.name, to=[app.mail_address],
                                     subject=("AW: " if de else "RE: ") + sent.subject, body=body,
                                     sent_at=app.now(ws.state.current_date), thread_id=sent.thread_id,
                                     in_reply_to=sent.mail_id))


# ---------------------------------------------------------------- colleagues in the team chat

def _resident_in(care: CareState, text: str):
    low = text.casefold()
    for r in care.residents:
        if r.status != "present":
            continue
        if re.search(rf"\b{re.escape(r.room.casefold())}\b", low) or (r.family_name.casefold() in low and
                                                                      (r.given_name.casefold() in low or re.search(r"(frau|herr|herrn|mrs|mr|ms)\.? " + re.escape(r.family_name.casefold()), low))):
            return r
    return None


def care_chat_reply(ws, post, channel) -> bool:
    """A colleague asked about care work for a resident ("kannst du um 10 den Verband bei Frau X machen?") answers by
    rule: not on shift, not allowed (qualification or skill), or yes. Nothing changes in the plan – assigning stays
    the nurse in charge's job. Returns False when the post is not about care work (Workforce answers then)."""
    from datetime import datetime

    from opsdesk.fullcare import dailyplan
    from opsdesk.workforce.colleagues import _post
    care, app, st = ws.care, ws.app, ws.state
    if care is None:
        return False
    low = post.text.casefold()
    entry = next((c for c in care.standard.catalogue if any(lbl.casefold() in low for lbl in c.label.values())), None)
    resident = _resident_in(care, post.text)
    if entry is None or resident is None:
        return False
    staff = {e.employee_id: e for e in st.employees}
    if channel.kind == "direct":
        responders = [m for m in channel.members if m in staff][:1]
    else:
        responders = [eid for eid in channel.members if eid in staff and
                      re.search(rf"\b{re.escape(staff[eid].name.split(' ')[0].casefold())}\b", low)][:2]
    if not responders:
        return False
    de = app.locale == "de-DE"
    now = datetime.strptime(app.now(st.current_date), "%Y-%m-%d %H:%M")
    day, shift = dailyplan.shift_at(now)
    on = {s.employee_id: s for s in dailyplan.on_shift(st, care, day, shift)}
    probe = dailyplan.PlanItem("x", day, shift.value, resident.ward_id, resident.resident_id, now, "", entry.key,
                               entry.duration_min, entry.qualification, tuple(entry.skills), "extra", "x")
    for who in responders:
        reasons = dailyplan.eligibility(probe, on.get(who))
        if "not_on_shift" in reasons:
            text = "Ich bin heute nicht im Dienst, sorry." if de else "I'm not on shift today, sorry."
        elif "qualification" in reasons:
            text = ("Das darf ich nicht – dafür braucht es eine Pflegefachkraft." if de else
                    "I'm not allowed to do that – it needs a registered nurse.")
        elif reasons:
            text = ("Dafür habe ich die Zusatzqualifikation nicht." if de else "I don't have the training for that one.")
        else:
            text = _pick(f"{post.chat_id}:{who}", ["Ja, mache ich.", "Klar, übernehme ich."] if de else
                         ["Yes, I'll do it.", "Sure, I'll take it."])
        _post(ws, channel.channel_id, who, text, post.chat_id)
    return True
