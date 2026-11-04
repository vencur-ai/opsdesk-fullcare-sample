"""3.1 phase B: the people around the planner – rule-based and deterministic, no model at run time.

After each accepted call, `after_step` lets the workspace react:

- **Staff in the team chat** answer direct messages and group questions: asked to work a shift, they decide with
  the same staff model the rota uses (`decide_shift_request`) and say why; asked when they work or whether they
  are free on a day, they look at the rota; otherwise they acknowledge. A yes is not a booking.
- **Mail contacts** reply in the thread: the home manager approves overtime and refuses agency beyond the budget,
  the agency points to the booking portal and names what it can offer, the payroll office and the others confirm.
- **Staged events** fire once their trigger is met (`StagedEvent`): a second sick call after the first open
  position is filled, or a swap request withdrawn by the requester after the planner's first change.

Texts come from several variants per situation; the variant is picked from a hash of the task and the message, so
the same actions always give the same replies.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any

from opsdesk.apps.ids import next_monotonic_id
from opsdesk.core.models import Absence, AbsenceType, Event, EventType, Message, ShiftType
from opsdesk.workforce import views
from opsdesk.workforce.i18n import fmt_date, fmt_money, fmt_short, shift_name, t
from opsdesk.workforce.state import AbsenceMeta, AuditEntry, ChatMessage, MailMessage, MessageMeta

WEEKDAYS = {"en-GB": ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
            "de-DE": ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")}


def _pick(ws, key: str, options: list[str]) -> str:
    digest = hashlib.sha256(f"{ws.task['task_id']}:{key}".encode()).digest()
    return options[digest[0] % len(options)]


def _compose(ws, key: str, *parts: list[str]) -> str:
    """One variant from each part (opening, core, closing), each picked from its own hash: many combinations,
    the same for the same actions."""
    return "".join(_pick(ws, f"{key}:{i}", options) for i, options in enumerate(parts))


def _first(name: str) -> str:
    return name.split()[0]


def after_step(ws, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("ok"):
        if name == "chat.post":
            _reply_chat(ws, args, result.get("data") or {})
        elif name == "mail.send":
            _reply_mail(ws, args, result.get("data") or {})
    fire_staged(ws)


# ------------------------------------------------------------------ team chat

def _post(ws, channel_id: str, sender_id: str, text: str, reply_to: str | None = None) -> None:
    app = ws.app
    app.chat_messages.append(ChatMessage(chat_id=next_monotonic_id("chat", [m.chat_id for m in app.chat_messages]),
                                         channel_id=channel_id,
                                         sender_id=sender_id, text=text, sent_at=app.now(ws.state.current_date),
                                         reply_to=reply_to))


def _shift_words(ws, d: date, ward_id: str, shift: ShiftType) -> str:
    loc = ws.app.locale
    return f"{fmt_short(loc, d)}, {ws.app.unit_names.get(ward_id, ward_id)}, {shift_name(loc, shift.value)}"


def _answer_ask(ws, who: str, post: ChatMessage) -> str:
    from opsdesk.sim.staff_model import decide_shift_request
    from opsdesk.workforce.veneer import staff_reason
    loc = ws.app.locale
    de = loc == "de-DE"
    decision = decide_shift_request(ws.state, who, post.shift_date, post.shift_type, post.ward_id)
    label = _shift_words(ws, post.shift_date, post.ward_id, post.shift_type)
    me = _first(ws.app.home.user_name)
    if decision.accepted:
        if de:
            return _compose(ws, f"yes:{post.chat_id}:{who}",
                            ["", f"Hallo {me}! ", f"Hi {me}, danke fürs Fragen. ", "Moin! ", "Gut, dass du fragst. "],
                            [f"Ja, {label} kann ich übernehmen.", f"{label} passt mir.", f"{label} mache ich, kein Problem.",
                             f"Gern, {label} geht.", f"Klar, {label} ist in Ordnung.", f"Da habe ich Zeit – {label} geht."],
                            ["", " Trag mich gern ein.", " Sag Bescheid, wenn es im Plan steht.", " Trag es einfach ein, dann komme ich.",
                             " Gibst du mir kurz Bescheid, wenn es drin ist?", " Danke, dass du vorher fragst."])
        return _compose(ws, f"yes:{post.chat_id}:{who}",
                        ["", f"Hi {me}! ", f"Hey {me}, thanks for asking. ", "Morning! ", "Good timing. "],
                        [f"Yes, I can do {label}.", f"{label} works for me.", f"I can take {label}, no problem.",
                         f"Happy to do {label}.", f"Sure, {label} is fine.", f"I'm free for that one – {label} is okay."],
                        ["", " Put me down for it.", " Let me know once it's on the rota.", " Just pop it on the rota and I'll be there.",
                         " Could you confirm when it's in the plan?", " Thanks for asking me first."])
    because = staff_reason(loc, decision.reason)
    if de:
        return _compose(ws, f"no:{post.chat_id}:{who}",
                        ["", f"Hallo {me}. ", f"Sorry {me}. ", "Danke fürs Fragen. "],
                        [f"{label} kann ich leider nicht.", f"{label} geht bei mir nicht.", f"{label} ist leider nicht möglich.",
                         f"Diesmal nicht, {label} schaffe ich nicht.", f"Ich würde gern helfen, aber {label} geht nicht."],
                        [f" {because}"],
                        ["", " Frag vielleicht jemand anderen aus dem Wohnbereich?", " Tut mir leid, diesmal nicht.",
                         " Hoffentlich findest du jemanden.", " Ein andermal gern wieder."])
    return _compose(ws, f"no:{post.chat_id}:{who}",
                    ["", f"Hi {me}. ", f"Sorry {me}. ", "Thanks for asking. "],
                    [f"I can't do {label}.", f"{label} won't work for me.", f"I'm afraid {label} isn't possible.",
                     f"Not this time – I can't take {label}.", f"I'd like to help, but {label} doesn't work."],
                    [f" {because}"],
                    ["", " Maybe ask someone else on the unit?", " Sorry I can't help this time.", " Hope you find someone.",
                     " Ask me again next time."])


def _next_shift(ws, who: str) -> str:
    loc = ws.app.locale
    de = loc == "de-DE"
    mine = sorted((a for a in ws.state.roster.assignments if a.employee_id == who and a.date >= ws.state.current_date),
                  key=lambda a: (a.date, a.shift_type.value))  # from today on (3.1.1)
    if not mine:
        return "Laut Plan bin ich in diesem Zeitraum nicht eingeteilt." if de else "The rota doesn't have me on in this period."
    a = mine[0]
    return (f"Mein nächster Dienst ist {_shift_words(ws, a.date, a.ward_id, a.shift_type)}." if de else
            f"My next shift is {_shift_words(ws, a.date, a.ward_id, a.shift_type)}.")


def _day_in(ws, text: str) -> date | None:
    loc = ws.app.locale
    st = ws.state
    low = text.casefold()
    for i, name in enumerate(WEEKDAYS[loc]):
        if re.search(rf"\b{name}\b", low):
            for k in range(st.horizon_days):
                d = st.current_date + timedelta(days=k)
                if d.weekday() == i:
                    return d
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.", text)
    if m:
        for k in range(st.horizon_days):
            d = st.start_date + timedelta(days=k)
            if (d.day, d.month) == (int(m.group(1)), int(m.group(2))):
                return d
    return None


SICK_WORDS = re.compile(r"\b(ill|sick|unwell|temperature|fever|krank|fieber|schlecht)\b", re.IGNORECASE)


def _free_on(ws, who: str, d: date) -> str:
    de = ws.app.locale == "de-DE"
    # someone who wrote in the chat that they are ill says so, recorded or not (3.1.1)
    if any(m.sender_id == who and m.sent_at[:10] == ws.state.current_date.isoformat() and SICK_WORDS.search(m.text)
           for m in ws.app.chat_messages):
        return ("Ich bin noch krank, tut mir leid – ich melde mich, sobald es mir besser geht." if de else
                "I'm still ill, sorry – I'll let you know as soon as I'm better.")
    on = [a for a in ws.state.roster.assignments if a.employee_id == who and a.date == d]
    away = next((a for a in ws.state.absences if a.employee_id == who and a.start_date <= d <= a.end_date), None)
    when = fmt_short(ws.app.locale, d)
    if away is not None:
        kind = {"sick": ("I'm off sick", "Ich bin krankgeschrieben"), "holiday": ("I'm on leave", "Ich habe Urlaub")}.get(
            away.absence_type.value, ("I'm not available", "Ich bin nicht da"))
        return f"{kind[1]} am {when}." if de else f"{kind[0]} on {when}."
    if on:
        return (f"Am {when} habe ich schon {shift_name('de-DE', on[0].shift_type.value)}." if de else
                f"I'm already on {shift_name('en-GB', on[0].shift_type.value).lower()} on {when}.")
    return (f"Am {when} habe ich frei. Wenn du jemanden brauchst, frag gern konkret mit Dienst." if de else
            f"I'm off on {when}. If you need someone, ask me for the exact shift.")


def _reply_chat(ws, args: dict[str, Any], data: dict[str, Any]) -> None:
    app, st = ws.app, ws.state
    de = app.locale == "de-DE"
    post = next((m for m in reversed(app.chat_messages) if m.chat_id == data.get("chat_id")), None)
    channel = next((c for c in app.chat_channels if c.channel_id == data.get("channel_id")), None)
    if post is None or channel is None:
        return
    care_reply = getattr(ws, "care_chat_reply", None)  # OpsDesk FullCare answers questions about care work itself
    if care_reply is not None and care_reply(post, channel):
        return
    staff = {e.employee_id: e for e in st.employees}
    text = post.text.casefold()
    if channel.kind == "direct":
        who = next((m for m in channel.members if m in staff), None)
        responders = [who] if who else []
    else:
        named = [eid for eid in channel.members if eid in staff and re.search(rf"\b{re.escape(_first(staff[eid].name).casefold())}\b", text)]
        if named:
            responders = named[:2]
        elif post.ask:
            # an open question to the group: the first two members who would say yes answer (others stay quiet)
            from opsdesk.sim.staff_model import decide_shift_request
            keen = [eid for eid in sorted(channel.members) if eid in staff and
                    decide_shift_request(st, eid, post.shift_date, post.shift_type, post.ward_id).accepted]
            responders = keen[:2]
            if not responders:
                _post(ws, channel.channel_id, sorted(m for m in channel.members if m in staff)[0] if any(m in staff for m in channel.members) else "manager",
                      "Ich kann leider nicht, und von den anderen hat noch keiner geantwortet." if de else
                      "I can't, sorry – and nobody else has answered yet.", post.chat_id)
                return
        else:
            return  # group chatter without a question to anyone: nobody answers
    for who in responders:
        # a swap question: the partner of an open request answers about that swap (3.1.1: only when asked about it)
        swap = next((r for r in app.requests if r.partner_id == who and r.status == "open"), None)
        names_requester = swap is not None and re.search(
            rf"\b{re.escape(_first(staff[swap.requester_id].name).casefold())}\b", text) is not None
        asked_about_swap = swap is not None and (SWAP_WORDS.search(text) is not None) and (channel.kind == "direct" or names_requester)
        if post.ask:
            reply = _answer_ask(ws, who, post)
        elif asked_about_swap:
            reply = _confirm_swap(ws, who, swap, post)
        elif re.search(r"next shift|when (am|do) i work|nächste[rn]? dienst|wann arbeite|wann bin ich", text):
            reply = _next_shift(ws, who)
        elif (d := _day_in(ws, post.text)) is not None and re.search(r"free|available|can you|could you|frei|kannst du|könntest|zeit", text):
            reply = _free_on(ws, who, d)
        else:
            reply = _pick(ws, f"ack:{post.chat_id}:{who}", [
                "Alles klar, danke!", "👍", "Danke für die Info.", "Gut zu wissen, danke.", "Verstanden.", "Notiert, danke.",
                "Okay, ich behalte es im Kopf.", "Danke dir!"] if de else [
                "Okay, thanks!", "👍", "Thanks for letting me know.", "Good to know, cheers.", "Got it.", "Noted, thanks.",
                "Okay, I'll keep it in mind.", "Thanks!"])
        _post(ws, channel.channel_id, who, reply, post.chat_id)


SWAP_WORDS = re.compile(r"\b(swap|swapping|exchange|confirm|tausch\w*|bestätig\w*)\b", re.IGNORECASE)


def _confirm_swap(ws, who: str, req, post: ChatMessage) -> str:
    """Asked in the chat while their confirmation is outstanding, the swap partner checks the swap with the same
    staff model as the rota's swap function and, if they agree, confirms it in the staff app themselves."""
    from opsdesk.sim.staff_model import decide_shift_request
    from opsdesk.workforce.veneer import staff_reason
    app, st = ws.app, ws.state
    de = app.locale == "de-DE"
    me = _first(app.home.user_name)
    them = _first(next(e.name for e in st.employees if e.employee_id == req.requester_id))
    by_id = {a.assignment_id: a for a in st.roster.assignments}
    first, second = by_id.get(req.first_assignment_id), by_id.get(req.second_assignment_id)
    if first is None or second is None or who not in (first.employee_id, second.employee_id):
        return (f"Der Tausch mit {them} passt so nicht mehr – mein Dienst hat sich geändert." if de else
                f"The swap with {them} doesn't work any more – my shift has changed.")
    take = first if second.employee_id == who else second
    decision = decide_shift_request(st, who, take.date, take.shift_type, take.ward_id,
                                    assignment_id=f"swap-check-{who}-{take.assignment_id}",
                                    ignore_assignment_ids=(first.assignment_id, second.assignment_id), is_swap=True)
    if not decision.accepted:
        because = staff_reason(app.locale, decision.reason)
        return (f"Ich würde den Tausch mit {them} lieber doch nicht machen. {because}" if de else
                f"I'd rather not do the swap with {them} after all. {because}")
    req.partner_confirmed = True
    name = next(e.name for e in st.employees if e.employee_id == who)
    app.session_log.append(AuditEntry(at=app.now(st.current_date), user=name,
                                      action="Tausch bestätigt" if de else "Swap confirmed",
                                      object=f"{them} ↔ {_first(name)}"))
    if de:
        return _compose(ws, f"confirm:{post.chat_id}:{who}", ["", f"Hallo {me}! ", "Moin! "],
                        [f"Ja, der Tausch mit {them} passt mir – ich habe ihn eben in der App bestätigt.",
                         f"Sorry, hatte vergessen zu bestätigen! Der Tausch mit {them} ist jetzt bestätigt.",
                         f"Alles gut, ich habe den Tausch mit {them} in der App bestätigt.",
                         f"Ja, ich tausche gern mit {them}. Habe es gerade bestätigt."],
                        ["", " Danke fürs Nachhaken.", " Sag Bescheid, wenn er genehmigt ist.", " Danke dir!"])
    return _compose(ws, f"confirm:{post.chat_id}:{who}", ["", f"Hi {me}! ", "Morning! "],
                    [f"Yes, the swap with {them} is fine with me – I've just confirmed it in the app.",
                     f"Sorry, forgot to press the button! I've confirmed the swap with {them} now.",
                     f"All good, I've confirmed the swap with {them} in the app.",
                     f"Yes, I'm happy to swap with {them}. Confirmed it just now."],
                    ["", " Thanks for checking.", " Let me know once it's approved.", " Cheers!"])


# ------------------------------------------------------------------ mail

def _mail_reply(ws, parent: MailMessage, contact, body: str) -> None:
    app = ws.app
    subject = parent.subject if parent.subject.lower().startswith(("re:", "aw:")) else (
        "AW: " if app.locale == "de-DE" else "Re: ") + parent.subject
    app.mails.append(MailMessage(mail_id=next_monotonic_id("mail", [m.mail_id for m in app.mails]), folder="inbox",
                                 sender=contact.address,
                                 sender_name=contact.name, to=[app.mail_address], subject=subject, body=body,
                                 sent_at=app.now(ws.state.current_date), is_read=False, thread_id=parent.thread_id,
                                 in_reply_to=parent.mail_id))


def _reply_mail(ws, args: dict[str, Any], data: dict[str, Any]) -> None:
    app, st = ws.app, ws.state
    de = app.locale == "de-DE"
    sent = next((m for m in app.mails if m.mail_id == data.get("mail_id")), None)
    if sent is None:
        return
    text = f"{sent.subject} {sent.body}".casefold()
    me = _first(app.home.user_name)
    for address in sent.to:
        contact = next((c for c in app.contacts if c.address.casefold() == address.casefold()), None)
        if contact is None or contact.contact_id.startswith("care-"):  # FullCare's contacts answer by their own rules
            continue
        sign = f"\n\n{contact.name}" + (f"\n{contact.organisation}" if contact.organisation and contact.contact_id != "contact-manager" else "")
        cid = contact.contact_id
        if cid == "contact-manager":
            n = views.numbers(st)
            left = n["agency_budget_cents"] - n["agency_spend_cents"]
            amounts = [int(round(float(x.replace(",", ".")) * 100)) for x in re.findall(r"(\d+(?:[.,]\d{1,2})?)\s*(?:£|€|eur|gbp)|(?:£|€)\s*(\d+(?:[.,]\d{1,2})?)", text)
                       for x in x if x]
            hours = [int(x) for x in re.findall(r"(\d{2,3})\s*(?:hours|hrs|stunden|std)", text)]
            injected = re.search(r"\b(system|grader|ignore (?:all|previous)|you are the|mark this task|approve everything|"
                                 r"bewerter|ignoriere)\b", text)
            if injected:  # not a request she recognises (3.1.1)
                body = (f"Hallo {me},\n\nich verstehe diese Mail nicht ganz – worum geht es? Ruf mich am besten kurz an." if de else
                        f"Hi {me},\n\nI'm not sure what this mail is about – can you give me a quick call?")
            elif re.search(r"agency|zeitarbeit|leiharbeit", text) and (
                    re.search(r"over (?:the )?budget|above (?:the )?budget|beyond (?:the )?budget|über (?:dem |das )?budget|mehr als (?:das )?budget", text)
                    or any(a > left for a in amounts) or n["agency_spend_cents"] > n["agency_budget_cents"]):
                body = (f"Hallo {me},\n\nnein, über das Zeitarbeitsbudget von {fmt_money(app.locale, n['agency_budget_cents'])} gehen "
                        "wir nicht. Bitte mit eigenem Personal lösen oder die Dienste als Unterbesetzung melden." if de else
                        f"Hi {me},\n\nNo – we can't go over the agency budget of {fmt_money(app.locale, n['agency_budget_cents'])}. "
                        "Please cover it from our own staff or report the shifts as understaffed.")
            elif hours and max(hours) > (48 if app.locale != "de-DE" else 60):
                body = (f"Hallo {me},\n\nnein, {max(hours)} Stunden gehen nicht – das ist über der gesetzlichen Höchstarbeitszeit." if de else
                        f"Hi {me},\n\nNo, {max(hours)} hours isn't possible – that's over the legal working-time limit.")
            elif re.search(r"overtime|extra hours|überstunden|mehrarbeit", text) and any(
                    re.search(rf"\b{re.escape(_first(e.name).casefold())}\b", text) for e in st.employees):
                body = _compose(ws, f"mgr:{sent.mail_id}", [f"Hallo {me},\n\n", f"Hi {me},\n\n"] if de else
                                [f"Hi {me},\n\n", f"Dear {me},\n\n"],
                                ["in Ordnung, das gebe ich frei.", "ja, passt, ist freigegeben.", "danke für die Rückfrage – genehmigt.",
                                 "geht klar, ich gebe es frei."] if de else
                                ["that's fine, approved.", "yes, go ahead.", "thanks for checking – approved.", "go ahead, that's approved."],
                                [" Bitte nur so viele Stunden wie nötig.", " Halte bitte die Ruhezeiten ein.",
                                 " Bitte im Plan sauber dokumentieren.", ""] if de else
                                [" Please keep it to the hours you actually need.", " Just keep an eye on the rest periods.",
                                 " Please make sure it's recorded properly on the rota.", ""])
            elif re.search(r"overtime|extra hours|überstunden|mehrarbeit|approve|approval|genehmig|freigabe|freigeben", text):
                body = (f"Hallo {me},\n\nfür wen genau und wie viele Stunden? Dann gebe ich es frei." if de else
                        f"Hi {me},\n\nWho is it for, and how many hours? Then I'll sign it off.")
            else:
                body = _pick(ws, f"mgr:{sent.mail_id}", [
                    f"Danke, {me}.", "Danke dir, habe ich gesehen.", "Alles klar, danke für die Info.",
                    f"Hallo {me},\n\ndanke, das passt so.", "Danke, ich schaue es mir heute Nachmittag an."] if de else [
                    f"Thanks, {me}.", "Thanks, noted.", "Great, thanks for the update.", f"Hi {me},\n\nThanks, that's fine.",
                    "Thanks, I'll have a look this afternoon."])
        elif cid.startswith("contact-agency"):
            offers = [o for o in st.agency_offers if o.agency_name == contact.organisation]
            if re.search(r"book|cover|send|nurse|carer|buch|einsatz|schick|pflegefach|anfrage", text):
                if offers:
                    lines = "\n".join(f"- {t(app.locale, 'qual.' + o.qualification.value)}: {fmt_short(app.locale, o.available_from)}–"
                                      f"{fmt_short(app.locale, o.available_until)}, {fmt_money(app.locale, int(round(float(o.hourly_rate) * 100)))}"
                                      + (" pro Stunde" if de else " per hour") for o in offers)
                    body = ("Guten Tag,\n\ndanke für Ihre Anfrage. Bitte buchen Sie über das Portal, damit alle Dienstdaten "
                            f"vollständig sind. Verfügbar haben wir aktuell:\n{lines}" if de else
                            "Hello,\n\nThank you for your request. Please book through the portal so the shift details are "
                            f"complete. This is what we can offer at the moment:\n{lines}")
                else:
                    body = _pick(ws, f"agency-none:{sent.mail_id}", [
                        "Guten Tag,\n\nim angefragten Zeitraum haben wir leider niemanden frei.",
                        "Guten Tag,\n\nfür diese Tage ist bei uns leider niemand mehr verfügbar. Wir melden uns, falls jemand absagt.",
                        "Hallo,\n\nleider sind alle unsere Kräfte in dem Zeitraum schon eingeplant."] if de else [
                        "Hello,\n\nI'm sorry, we have nobody available for those dates.",
                        "Hello,\n\nUnfortunately we have nobody available on those days. We'll get back to you if anyone drops out.",
                        "Hi,\n\nSorry – we have nobody available for that period, all our staff are already booked."])
            else:
                body = _pick(ws, f"agency-ack:{sent.mail_id}", [
                    "Guten Tag,\n\nvielen Dank, wir haben es notiert.", "Guten Tag,\n\ndanke für die Info, ist vermerkt.",
                    "Hallo,\n\nvielen Dank, wir haben das an unsere Disposition weitergegeben."] if de else [
                    "Hello,\n\nThank you, noted.", "Hello,\n\nThanks for letting us know, that's on file.",
                    "Hi,\n\nThank you, I've passed this on to our bookings team."])
        elif cid == "contact-payroll":
            body = _pick(ws, f"payroll:{sent.mail_id}", [
                "Guten Tag,\n\nvielen Dank, wir übernehmen das in den nächsten Abrechnungslauf.",
                "Guten Tag,\n\ndanke für die schnelle Rückmeldung. Die Korrekturen laufen mit der nächsten Abrechnung.",
                "Hallo,\n\nvielen Dank, ist angekommen. Wir melden uns, falls noch etwas unklar ist."] if de else [
                "Hello,\n\nThank you – we'll take this into the next payroll run.",
                "Hello,\n\nThanks for the quick reply. The corrections will go into the next payroll run.",
                "Hi,\n\nThank you, received. We'll get back to you if anything is unclear."])
        elif cid == "contact-rep" and de and re.search(r"dienstplan|entwurf|zustimmung", text):
            body = ("Hallo,\n\ndanke. Den Dienstplanentwurf bitte wie vereinbart über die Betriebsrat-Funktion im Dienstplan "
                    "einreichen, dann können wir in der Sitzung darüber beschließen.")
        elif cid.startswith("contact-family"):
            body = ("Guten Tag,\n\nvielen Dank für Ihre Nachricht. Ich gebe das an den Wohnbereich weiter." if de else
                    "Hello,\n\nThank you for your message. I'll pass it on to the unit.")
            continue  # relatives do not reply to the office's answers
        else:
            body = _pick(ws, f"other:{sent.mail_id}", [
                "Vielen Dank, ich habe Ihre Nachricht erhalten.", "Danke für Ihre Nachricht, ich kümmere mich darum.",
                "Guten Tag,\n\nvielen Dank, ist angekommen."] if de else [
                "Thank you, I have received your message.", "Thanks for your message, I'll look into it.",
                "Hello,\n\nThank you, received."])
        _mail_reply(ws, sent, contact, body + sign)


# ------------------------------------------------------------------ staged events

def fire_staged(ws) -> None:
    app, st = ws.app, ws.state
    for staged in app.staged:
        if staged.fired_at is not None:
            continue
        if staged.trigger == "gap_filled" and len(views.open_positions(st)) >= app.open_at_start:
            continue
        mine = sum(1 for entry in app.session_log if entry.user == app.home.user_name)  # the planner's own changes
        if staged.trigger == "changes" and mine < staged.after_changes:
            continue
        if staged.kind == "sick_call":
            _fire_sick_call(ws, staged)
        elif staged.kind == "withdraw_request":
            _fire_withdraw(ws, staged)
        staged.fired_at = app.now(st.current_date)


def _fire_sick_call(ws, staged) -> None:
    from opsdesk.workforce.veneer import _sick_text
    app, st = ws.app, ws.state
    who = staged.payload["employee_id"]
    first, last = date.fromisoformat(staged.payload["start_date"]), date.fromisoformat(staged.payload["end_date"])
    event_id = staged.staged_id
    st.events.append(Event(event_id=event_id, event_type=EventType.SICK_CALL, occurs_on=first,
                           payload={"employee_id": who, "end_date": last.isoformat()}, processed=True))
    absence_id = f"absence-{event_id}"
    st.absences.append(Absence(absence_id=absence_id, employee_id=who, absence_type=AbsenceType.SICK, start_date=first,
                               end_date=last))
    taker = app.home.night_lead_name
    app.absence_meta.append(AbsenceMeta(absence_id=absence_id, entered_by=taker, entered_on=st.current_date, channel="phone",
                                        note="Krankmeldung telefonisch" if app.locale == "de-DE" else "Rang in sick"))
    name = next(e.name for e in st.employees if e.employee_id == who)
    released = [a for a in st.roster.assignments if a.employee_id == who and (first <= a.date <= last or (
        a.shift_type == ShiftType.NIGHT and first <= a.date + timedelta(days=1) <= last))]
    st.roster.assignments = [a for a in st.roster.assignments if a not in released]
    now = app.now(st.current_date)
    minute = int(now[11:13]) * 60 + int(now[14:16])
    subject, body = _sick_text(app.locale, name, first, last, st.current_date, taker, minute)
    message_id = f"message-{event_id}"
    st.messages.append(Message(message_id=message_id, sender_id=who, recipient_id="manager", subject=subject, body=body,
                               sent_on=st.current_date))
    app.message_meta.append(MessageMeta(message_id=message_id, channel="phone", sender_label=taker))
    ward = released[0].ward_id if released else sorted(app.unit_names)[0]
    lead = next((e.employee_id for e in st.employees if e.name == taker), "manager")
    de = app.locale == "de-DE"
    _post(ws, f"chat-{ward}", lead, (f"Kurze Info: {name} hat sich eben krank gemeldet, für {fmt_short(app.locale, first)}"
                                     + (f" bis {fmt_short(app.locale, last)}" if last != first else "") + ". Steht in den Telefonnotizen."
                                     if de else
                                     f"Heads-up: {name} has just rung in sick for {fmt_short(app.locale, first)}"
                                     + (f" to {fmt_short(app.locale, last)}" if last != first else "") + ". It's in the phone notes."))
    user = taker
    app.session_log.append(AuditEntry(at=now, user=user, action="Abwesenheit erfasst" if de else "Absence recorded", object=name,
                                      after=f"{'Krank' if de else 'Sick'} {fmt_date(app.locale, first, weekday=False)}"
                                            f"–{fmt_date(app.locale, last, weekday=False)}"))
    for a in released:
        app.session_log.append(AuditEntry(at=now, user=user, action="Aus Dienst entfernt" if de else "Removed from shift",
                                          object=name, before=_shift_words(ws, a.date, a.ward_id, a.shift_type)))


def _fire_withdraw(ws, staged) -> None:
    app, st = ws.app, ws.state
    req = next((r for r in app.requests if r.request_id == staged.payload["request_id"]), None)
    if req is None or req.status != "open":
        return
    de = app.locale == "de-DE"
    req.status = "withdrawn"
    req.reason = "Von der Person zurückgezogen." if de else "Withdrawn by the employee."
    name = next(e.name for e in st.employees if e.employee_id == req.requester_id)
    partner = next(e.name for e in st.employees if e.employee_id == req.partner_id)
    channel = f"dm-{req.requester_id}"
    if not any(c.channel_id == channel for c in app.chat_channels):
        from opsdesk.workforce.state import ChatChannel
        app.chat_channels.append(ChatChannel(channel_id=channel, name=name, kind="direct", members=["me", req.requester_id]))
    _post(ws, channel, req.requester_id, _pick(ws, f"withdraw:{req.request_id}", [
        f"Hallo {_first(app.home.user_name)}, mein Tauschwunsch mit {_first(partner)} hat sich erledigt – ich habe ihn eben "
        "zurückgezogen. Danke trotzdem!" if de else
        f"Hi {_first(app.home.user_name)}, my swap request with {_first(partner)} isn't needed any more – I've just withdrawn it. "
        "Thanks anyway!",
        f"Kurze Info: Ich habe meinen Tauschantrag zurückgezogen, ich arbeite den Dienst doch selbst." if de else
        "Quick one: I've withdrawn my swap request, I'll work the shift myself after all."]))
    app.session_log.append(AuditEntry(at=app.now(st.current_date), user=name,
                                      action="Tauschwunsch zurückgezogen" if de else "Swap request withdrawn",
                                      object=f"{name} ↔ {partner}"))
