"""Realistic surface for a generated world: names, homes, units, documents, messages, records.

Build time (`apply_veneer`): rewrites the task bundle's world.json in the home's language and writes
app.json (profiles, absence records, request records, seeded audit history). Nothing the verifier
reads changes meaning: IDs, rules, demand, roster, absences and the house-rule policy stay as
generated; documents are re-rendered from the facts they stated (every number is kept).
Run time (`apply_runtime_texts`, `localize_new_messages`): messages announced at reset and staff
replies get the same natural texts.

All names are fictional combinations of common first and last names; addresses and phone numbers
use fictional towns and the UK drama number range.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from opsdesk.core.state import WorldState
from opsdesk.workforce.i18n import fmt_date, fmt_short, qual_name, shift_name
from opsdesk.workforce.state import (
    APP_FILE, AbsenceMeta, AuditEntry, EmployeeProfile, HomeInfo, MessageMeta, SwapRequest, WorkforceState,
)

LOCALES = {"uk": "en-GB", "de": "de-DE"}

GIVEN = {
    "en-GB": ("Amelia", "Olivia", "Isla", "Emily", "Sophie", "Grace", "Chloe", "Ella", "Lucy", "Hannah", "Megan",
              "Jessica", "Charlotte", "Ruth", "Joanne", "Karen", "Priya", "Aisha", "Fatima", "Zofia", "Agnieszka",
              "Mary", "Joy", "Chiamaka", "Blessing", "Maricel", "Rosalie", "James", "Oliver", "Jack", "Harry",
              "Thomas", "Daniel", "Ryan", "Liam", "Callum", "Mohammed", "Tomasz", "Kwame", "David", "Mark", "Paul",
              "Ben", "Joseph", "Samuel", "Rhys", "Gareth", "Imran", "Anil", "Ade"),
    "de-DE": ("Anna", "Maria", "Laura", "Julia", "Lena", "Sarah", "Katharina", "Sabine", "Petra", "Claudia",
              "Monika", "Ewa", "Agnieszka", "Swetlana", "Elif", "Ayşe", "Fatma", "Jana", "Mia", "Hannah", "Lea",
              "Nicole", "Kerstin", "Birgit", "Ioana", "Mihaela", "Lukas", "Jonas", "Leon", "Finn", "Paul", "Felix",
              "Maximilian", "Tobias", "Stefan", "Thomas", "Michael", "Andreas", "Mehmet", "Ahmet", "Marek",
              "Dmitri", "Jürgen", "Uwe", "Kai", "Daniel", "Florian", "Markus"),
}
FAMILY = {
    "en-GB": ("Smith", "Jones", "Williams", "Taylor", "Brown", "Davies", "Evans", "Wilson", "Thomas", "Roberts",
              "Johnson", "Lewis", "Walker", "Robinson", "Wood", "Thompson", "White", "Watson", "Jackson", "Wright",
              "Green", "Harris", "Cooper", "King", "Lee", "Martin", "James", "Morgan", "Hughes", "Edwards", "Hill",
              "Moore", "Patel", "Begum", "Okafor", "Adeyemi", "Mensah", "Kowalski", "Nowak", "Santos", "Reyes",
              "Fernandes", "Nair", "Kaur", "Bennett", "Shaw", "Foster", "Murphy"),
    "de-DE": ("Müller", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz", "Hoffmann",
              "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Wolf", "Schröder", "Neumann", "Schwarz",
              "Zimmermann", "Braun", "Krüger", "Hartmann", "Lange", "Werner", "Krause", "Lehmann", "Köhler",
              "Yılmaz", "Kaya", "Demir", "Nowak", "Popescu", "Petrović", "Lorenz", "Fuchs", "Keller",
              "Vogel", "Frank", "Berger", "Winkler", "Roth", "Beck", "Haas", "Sommer"),
}
# Every home has one pair of near-identical surnames and one pair of near-identical first names.
SIMILAR_FAMILY = {"en-GB": ("Clarke", "Clark"), "de-DE": ("Schmidt", "Schmitt")}
SIMILAR_GIVEN = {"en-GB": ("Sophia", "Sophie"), "de-DE": ("Anne", "Anna")}

HOMES = {
    "en-GB": (("Elm Court Care Home", "14 Mill Lane, Ashcombe Green"), ("Larkfield House", "2 Chapel Row, Hollinsford"),
              ("Beechwood Lodge", "88 Station Road, Marlow Heath"), ("Meadowbank Care Home", "5 Orchard Close, Brackley Vale"),
              ("Hollybrook House", "31 Church Street, Wenford"), ("Ashgrove Lodge", "7 Riverside Walk, Thornby Cross"),
              ("Fernlea Court", "19 Park View, Kelsham"), ("Juniper House", "40 High Street, Oldcastle Moor")),
    "de-DE": (("Haus am Lindenpark", "Lindenallee 12, 37213 Ebersfelde"), ("Seniorenzentrum Birkenhof", "Birkenweg 4, 54329 Wiesenbrunn"),
              ("Haus Sonnenhang", "Am Sonnenhang 9, 79843 Hohenreuth"), ("Pflegezentrum Eichengrund", "Eichengrund 1, 29364 Kleinbergen"),
              ("Seniorenresidenz Am Mühlbach", "Mühlbachstraße 22, 66957 Oberwalden"), ("Haus Rosental", "Rosentalweg 3, 91746 Altenstetten"),
              ("Pflegeheim St. Anna", "Kirchplatz 6, 84371 Steinach am Wald"), ("Haus Abendrot", "Gartenstraße 17, 06918 Mühlhausen-Ost")),
}
# 3.1.1: homes are put together from parts (about 400 names and 30 towns per country), all fictional
HOME_PARTS = {
    "en-GB": {"first": ("Elm", "Larkfield", "Beechwood", "Meadowbank", "Hollybrook", "Ashgrove", "Fernlea", "Juniper",
                        "Willowmere", "Oakridge", "Thornfield", "Rosedene", "Brookside", "Heatherley", "Maplehurst",
                        "Cedarwood", "Laurel", "Foxglove", "Kingsmead", "Orchard", "Bramley", "Linden", "Hawthorn",
                        "Sycamore"),
              "kind": ("Care Home", "House", "Lodge", "Court", "Manor", "Nursing Home", "View"),
              "streets": ("Mill Lane", "Chapel Row", "Station Road", "Orchard Close", "Church Street", "Riverside Walk",
                          "Park View", "High Street", "Green Lane", "Bridge Street", "Victoria Road", "Queens Avenue"),
              "towns": ("Ashcombe Green", "Hollinsford", "Marlow Heath", "Brackley Vale", "Wenford", "Thornby Cross",
                        "Kelsham", "Oldcastle Moor", "Ravensby", "Little Haddon", "Elmstead Marsh", "Coldbrook",
                        "Fairhaven Down", "Wickham Lees", "Stanbury Cross", "Hartley Bridge", "Northwold", "Ambersley",
                        "Kingswear Vale", "Dunmere", "Bramcote End", "Sedgefold", "Pennington Moor", "Holmwood Edge",
                        "Ferncombe", "Upper Lyndon", "Westbury Lees", "Calder Heath", "Saltmarsh End", "Greyfield")},
    "de-DE": {"kind": ("Haus", "Seniorenzentrum", "Pflegezentrum", "Seniorenresidenz", "Pflegeheim", "Altenzentrum"),
              "first": ("am Lindenpark", "Birkenhof", "Sonnenhang", "Eichengrund", "Am Mühlbach", "Rosental", "St. Anna",
                        "Abendrot", "Wiesengrund", "Kastanienhof", "Lerchenfeld", "Am Weinberg", "Talblick", "Buchenhain",
                        "St. Elisabeth", "Am Schlosspark", "Ahornhof", "Am Stadtgarten", "Lindenhaus", "Sonnenwiese",
                        "Am Kirchberg", "Waldfrieden", "Haus Anker", "Am Mühlenteich"),
              "streets": ("Lindenallee", "Birkenweg", "Am Sonnenhang", "Eichengrund", "Mühlbachstraße", "Rosentalweg",
                          "Kirchplatz", "Gartenstraße", "Wiesenweg", "Schulstraße", "Bergstraße", "Am Anger"),
              "towns": ("37213 Ebersfelde", "54329 Wiesenbrunn", "79843 Hohenreuth", "29364 Kleinbergen", "66957 Oberwalden",
                        "91746 Altenstetten", "84371 Steinach am Wald", "06918 Mühlhausen-Ost", "35287 Lindenau",
                        "48619 Rotenbühl", "72469 Hochfelden", "99734 Grünwalde", "56412 Kirchheiligen", "24891 Nordtal",
                        "83126 Bergstetten", "17398 Seefeld-Nord", "63785 Talbach", "47533 Wiesenfurt", "86972 Altenmoos",
                        "31195 Hainfeld", "59929 Brunnstadt", "04741 Elsterau", "77796 Kirchzell", "25917 Lüttenhof",
                        "93477 Waldmünster", "36452 Röhnfeld", "74251 Lauterbach-Süd", "15913 Sandfelde", "88316 Isnauen",
                        "51789 Lindlarbach")},
}


def _home(rng: random.Random, locale: str) -> tuple[str, str]:
    parts = HOME_PARTS[locale]
    first, kind, street, town = (rng.choice(parts[k]) for k in ("first", "kind", "streets", "towns"))
    if locale == "de-DE":
        name = first if first.startswith("Haus ") else f"{kind} {first}"
        return name, f"{street} {rng.randint(1, 48)}, {town}"
    return f"{first} {kind}", f"{rng.randint(1, 120)} {street}, {town}"


UNITS = {
    "en-GB": ("Rose Wing", "Oak Wing", "Willow Wing", "Maple Wing", "Bluebell Unit", "Primrose Wing", "Cedar Unit",
              "Harbour View", "Orchard Wing", "Elm Unit"),
    "de-DE": ("Wohnbereich Rosengarten", "Wohnbereich Eichenhain", "Wohnbereich Weidenweg", "Wohnbereich Ahorn",
              "Wohnbereich Sonnenblick", "Wohnbereich Lindenhof", "Wohnbereich Am Bach", "Wohnbereich Kastanie",
              "Wohnbereich Wiesengrund", "Wohnbereich Birke"),
}
DEMENTIA_UNIT = {"en-GB": ("Heather Unit (dementia care)", "Lavender Unit (dementia care)"),
                 "de-DE": ("Wohnbereich Lavendel (Demenz)", "Wohnbereich Heide (Demenz)")}
TITLES = {
    "en-GB": {"manager": "Home Manager", "user": "Rota coordinator", "clinical": "Deputy manager (clinical lead)",
              "ops": "Operations manager"},
    "de-DE": {"manager": "Pflegedienstleitung", "user": "Assistenz der Pflegedienstleitung",
              "clinical": "Wohnbereichsleitung", "ops": "Regionalleitung"},
}
AGENCIES = {"en-GB": ("Northstar Care Staffing", "Brightwell Nursing Agency", "Harbourside Care Recruitment"),
            "de-DE": ("Pflegepool Nord GmbH", "Personaldienst Altenhilfe Süd", "CuraFlex Zeitarbeit")}
CERTS = {"en-GB": ("First aid", "Moving and handling", "Fire safety", "Safeguarding adults"),
         "de-DE": ("Erste-Hilfe-Kurs", "Hygieneschulung", "Brandschutzunterweisung", "Expertenstandard Sturzprophylaxe")}
RN_CERT = {"en-GB": "NMC registration", "de-DE": "Berufsurkunde Pflegefachkraft"}


def _people(rng: random.Random, locale: str, count: int) -> list[str]:
    given, family = list(GIVEN[locale]), list(FAMILY[locale])
    used: set[str] = set()
    names: list[str] = []
    a, b = SIMILAR_FAMILY[locale]
    g1, g2 = SIMILAR_GIVEN[locale]
    planned = [f"{rng.choice(given)} {a}", f"{rng.choice(given)} {b}", f"{g1} {rng.choice(family)}",
               f"{g2} {rng.choice(family)}"]
    for name in planned:
        if name not in used:
            used.add(name)
            names.append(name)
    surnames: dict[str, int] = {}
    while len(names) < count:
        first, last = rng.choice(given), rng.choice(family)
        name = f"{first} {last}"
        # Real staff lists repeat surnames now and then, not four times in a small home; no "Thomas Thomas".
        if name in used or first == last or surnames.get(last, 0) >= 2:
            continue
        used.add(name)
        surnames[last] = surnames.get(last, 0) + 1
        names.append(name)
    names = names[:count]
    rng.shuffle(names)
    return names


def _first(name: str) -> str:
    return name.split(" ", 1)[0]


# ---------------------------------------------------------------------------------- documents

def _facts(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The facts the generated documents state, read back from their text (fail closed)."""
    def need(pattern: str, text: str) -> re.Match:
        m = re.search(pattern, text)
        if not m:
            raise ValueError(f"document veneer: pattern not found: {pattern}")
        return m
    house = docs["doc-house-rules"]["body"]
    facts: dict[str, Any] = {
        "house_nights": int(need(r"No more than (\d+) consecutive night shifts", house).group(1)),
        "house_rn": int(need(r"At least (\d+) registered nurse", house).group(1)),
        "house_days": int(need(r"No more than (\d+) consecutive working days", house).group(1)),
        "late_early": need(r"not permitted: (yes|no)", house).group(1) == "yes",
        "part_time": need(r"no more than ([\d.]+)%", house).group(1),
    }
    agreement = docs["doc-works-agreement"]["body"]
    facts["agreement_two_nights"] = "no more than two consecutive nights" in agreement
    email = docs["doc-manager-email"]["body"]
    facts["email_variant"] = "56h" if "56 hours" in email else "12h" if "12-hour" in email else None
    memo = docs["doc-expired-memo"]["body"]
    m = re.search(r"Allow up to (\d+) consecutive night duties", memo)
    facts["memo_nights"] = int(m.group(1)) if m else None
    facts["memo_deadline"] = "12:00 reply deadline" in memo
    facts["current_memo_rev2"] = "doc-current-memo" in docs and "15:00" in docs["doc-current-memo"]["body"]
    facts["current_memo_plain"] = "doc-current-memo" in docs and not facts["current_memo_rev2"]
    notes = docs["doc-ward-notes"]["body"]
    facts["dementia_note"] = "Dementia-unit handover" in notes
    reminders = notes.split("### Handover reminders", 1)
    facts["reminders"] = [line[2:].strip() for line in reminders[1].splitlines() if line.startswith("- ")] \
        if len(reminders) > 1 else []
    return facts


REMINDERS = {
    "Please keep a copy of the rota with the ward handover folder.":
        ("Please keep a printed copy of the rota in the handover folder.", "Bitte eine ausgedruckte Kopie des Dienstplans in den Übergabeordner legen."),
    "The printed board and electronic roster should show the same shift date.":
        ("The board in the office and the system must show the same dates.", "Die Plantafel im Dienstzimmer und das System müssen dieselben Daten zeigen."),
    "If a line is unclear, confirm the ward before messaging the employee.":
        ("If an entry is unclear, check the unit before you message the person.", "Wenn ein Eintrag unklar ist, erst den Wohnbereich prüfen, dann die Person anschreiben."),
    "A note in the margin does not alter a signed contract.":
        ("A handwritten note on the board does not change anyone's contract.", "Eine handschriftliche Notiz auf der Plantafel ändert keinen Arbeitsvertrag."),
    "Use the local date on all absence and swap messages.":
        ("Always use the date as shown on the rota in absence and swap messages.", "In Abwesenheits- und Tauschnachrichten immer das Datum wie im Dienstplan angeben."),
}


def _render_documents(world: dict[str, Any], locale: str, names: dict[str, str], units: list[str],
                      family: str = "") -> None:
    docs = {d["document_id"]: d for d in world["documents"]}
    f = _facts(docs)
    de = locale == "de-DE"
    uk = world["jurisdiction"] == "uk"
    nights_word = {2: ("two", "zwei"), 3: ("three", "drei"), 4: ("four", "vier"), 5: ("five", "fünf")}
    rows = [("early", "06:00", "14:00"), ("late", "14:00", "22:00"), ("night", "22:00", "06:00")]
    table = ("| Dienst | Beginn | Ende | Pause |\n|---|---:|---:|---:|\n" if de else
             "| Shift | Start | End | Break |\n|---|---:|---:|---:|\n") + "\n".join(
        f"| {shift_name(locale, s)} | {a} | {b} | 30 {'Minuten' if de else 'minutes'} |" for s, a, b in rows)
    if uk:
        law = [("Weekly hours: no more than 48 hours in any Monday–Sunday week, unless the person has signed the 48-hour opt-out.",
                "Wochenarbeitszeit: höchstens 48 Stunden je Woche (Montag bis Sonntag), außer bei unterschriebenem 48-Stunden-Verzicht."),
               ("Daily rest: at least 11 hours off between the end of one shift and the start of the next.",
                "Tägliche Ruhezeit: mindestens 11 Stunden zwischen Dienstende und nächstem Dienstbeginn."),
               ("Weekly rest: 24 hours off in a row in every 7 days, or 48 hours in every 14 days.",
                "Wöchentliche Ruhe: 24 Stunden am Stück in je 7 Tagen oder 48 Stunden in je 14 Tagen."),
               ("Breaks: every shift over six hours has at least 20 minutes' break (already built into our shifts).",
                "Pausen: jeder Dienst über sechs Stunden hat mindestens 20 Minuten Pause (in unseren Diensten enthalten)."),
               ("Night workers: no more than 8 hours' work on average per 24 hours.",
                "Nachtarbeit: im Durchschnitt höchstens 8 Stunden Arbeit je 24 Stunden.")]
    else:
        law = [("Daily working time (ArbZG § 3): no more than 10 working hours on one day.",
                "Tägliche Arbeitszeit (§ 3 ArbZG): höchstens 10 Stunden Arbeitszeit an einem Tag."),
               ("Breaks (ArbZG § 4): 30 minutes after six hours, 45 minutes after nine hours (built into our shifts).",
                "Pausen (§ 4 ArbZG): 30 Minuten nach sechs Stunden, 45 Minuten nach neun Stunden (in unseren Diensten enthalten)."),
               ("Daily rest (ArbZG § 5): at least 11 hours of uninterrupted rest between shifts.",
                "Ruhezeit (§ 5 ArbZG): mindestens 11 Stunden ununterbrochene Ruhe zwischen zwei Diensten."),
               ("Sundays (ArbZG § 11): at least 15 Sundays a year stay free. We apply this per planning period: of every five "
                "Sundays at most three may be worked, so at least floor(2 × Sundays in the period ÷ 5) Sundays stay free, but "
                "never more than all Sundays minus one. A Sunday counts as worked if any shift touches it.",
                "Sonntage (§ 11 ArbZG): mindestens 15 Sonntage im Jahr bleiben frei. Wir wenden das je Planungszeitraum an: "
                "von je fünf Sonntagen dürfen höchstens drei gearbeitet werden, also bleiben mindestens abgerundet "
                "(2 × Sonntage im Zeitraum ÷ 5) Sonntage frei, aber nie mehr als alle Sonntage minus einer. Ein Sonntag gilt "
                "als gearbeitet, wenn ein Dienst ihn berührt.")]
    records = [("Staff records are binding: nobody works on a fixed day off, outside a nights-only contract or during a recorded absence (sick calls included).",
                "Die Personalakte gilt: niemand arbeitet an einem festen freien Tag, außerhalb eines reinen Nachtdienstvertrags oder während einer eingetragenen Abwesenheit (Krankmeldungen eingeschlossen)."),
               ("Each staff record shows the maximum number of nights per month for that person.",
                "In jeder Personalakte steht die Höchstzahl an Nachtdiensten pro Monat."),
               ("Nights in a row, the nurse minimum per shift, working days in a row, late-to-early and part-time margins: see the current house rules and the signed works agreement.",
                "Nächte am Stück, Mindestzahl Pflegefachkräfte je Dienst, Arbeitstage am Stück, Spät-auf-Früh und Teilzeitgrenzen: siehe aktuelle Hausordnung und unterschriebene Betriebsvereinbarung.")]
    pick = 1 if de else 0
    precedence = ("### Welche Regel gilt?\nWenn zwei schriftliche Quellen sich widersprechen, gilt: Gesetz vor unterschriebener "
                  "Betriebsvereinbarung vor aktueller Hausordnung vor Rundschreiben. Ein abgelaufenes Rundschreiben gilt nicht. "
                  "Widersprechen sich zwei gültige Rundschreiben, gilt das neuere." if de else
                  "### Which rule applies?\nIf two written sources disagree, the order is: law, then the signed works agreement, "
                  "then the current house rules, then memos. An expired memo does not apply. If two valid memos disagree, the newer one applies.")
    docs["doc-handbook"].update(
        title="Auszug Mitarbeiterhandbuch: Arbeitszeit" if de else "Staff handbook (extract): working time",
        body=("## Arbeitszeit – was bei jedem Dienstplan gilt\n\n" if de else "## Working time – what every rota must follow\n\n")
        + "\n".join(f"- {x[pick]}" for x in law + records) + "\n\n" + ("## Dienstzeiten und Pausen\n\n" if de else
                                                                       "## Shift times and breaks\n\n") + table
        + "\n\n" + precedence)
    yes = f["late_early"]
    house_lines = [
        (f"No more than {f['house_nights']} night shifts in a row.", f"Höchstens {f['house_nights']} Nachtdienste am Stück."),
        (f"At least {f['house_rn']} registered nurse{'s' if f['house_rn'] != 1 else ''} on every shift on every unit.",
         f"In jedem Dienst und jedem Wohnbereich mindestens {f['house_rn']} Pflegefachkr{'äfte' if f['house_rn'] != 1 else 'aft'}."),
        (f"No more than {f['house_days']} working days in a row.", f"Höchstens {f['house_days']} Arbeitstage am Stück."),
        ("No early shift the morning after a late shift." if yes else "An early shift after a late shift is allowed.",
         "Kein Frühdienst am Morgen nach einem Spätdienst." if yes else "Frühdienst nach Spätdienst ist erlaubt."),
        (f"Part-time staff may work at most {f['part_time']}% above their contract hours in a Monday–Sunday week.",
         f"Teilzeitkräfte höchstens {f['part_time'].replace('.', ',')} % über ihren Vertragsstunden je Woche (Montag bis Sonntag)."),
    ]
    if family == "F03_swap_requests":
        house_lines.append(("Swap requests for shifts today or tomorrow are not approved; the office arranges cover instead.",
                            "Tauschwünsche für Dienste heute oder morgen werden nicht genehmigt; dann organisiert das Büro Ersatz."))
    docs["doc-house-rules"].update(title="Hausordnung Dienstplanung (aktuelle Fassung)" if de else "House rules for the rota (current copy)",
                                   body=("## Hausordnung Dienstplanung\n\n" if de else "## House rules for the rota\n\n")
                                   + "\n".join(f"- {x[pick]}" for x in house_lines)
                                   + ("\n\nWo die unterschriebene Betriebsvereinbarung oder das Gesetz etwas anderes festlegt, gilt "
                                      "das (Reihenfolge siehe Mitarbeiterhandbuch)." if de else
                                      "\n\nWhere the signed agreement or the law says otherwise, that applies (order of sources: "
                                      "see the staff handbook)."))
    agreement = ("## Betriebsvereinbarung Dienstplanung (unterschrieben)\n\nZwischen Geschäftsführung und Betriebsrat. Dienstpläne "
                 "werden dem Betriebsrat vor der Veröffentlichung zur Zustimmung vorgelegt. Wo diese Vereinbarung eine genauere Grenze "
                 "setzt als die Hausordnung, gilt die Vereinbarung; strengere gesetzliche Regeln bleiben." if de else
                 "## Agreement with staff representatives on rota planning (signed)\n\nBetween the provider and the elected staff "
                 "representatives. Where it sets a more specific limit than the house rules, the agreement applies; stricter legal "
                 "rules still apply.")
    if f["agreement_two_nights"]:
        agreement += ("\n\nNachtdienste: höchstens zwei Nächte am Stück, auch über das Wochenende hinweg. Weicht die Hausordnung davon ab, "
                      "gilt diese Vereinbarung." if de else
                      "\n\nNight shifts: no more than two nights in a row, also across a week boundary. Where the house rules say "
                      "something else, this agreement applies.")
    else:
        agreement += ("\n\nFür Nächte am Stück gilt die aktuelle Hausordnung. Genehmigte Tausche vor der nächsten Veröffentlichung eintragen."
                      if de else "\n\nFor nights in a row, the current house rules apply. Enter approved swaps before the next publication.")
    docs["doc-works-agreement"].update(title="Betriebsvereinbarung Dienstplanung" if de else "Staff representatives agreement (rota planning)",
                                       body=agreement)
    email = ("## Weitergeleitete E-Mail\n\nVon: Regionalleitung\nAn: Pflegedienstleitung\nBetreff: Besetzung in den nächsten Wochen\n\n"
             "Hallo zusammen, die Lage ist bei uns allen angespannt. Bitte haltet die Dienste besetzt, so gut es geht." if de else
             "## Forwarded email\n\nFrom: Operations manager\nTo: Home manager\nSubject: Cover over the next few weeks\n\n"
             "Hi all, staffing is tight across the region at the moment. Please keep the shifts covered as best you can.")
    if f["email_variant"] == "56h":
        email += ("\n\n„Diesen Monat sind 56 Stunden pro Woche für alle in Ordnung, wenn es nicht anders geht.“" if de else
                  "\n\n“For this month, 56 hours a week should be fine for everyone if there's no other way.”")
    elif f["email_variant"] == "12h":
        email += ("\n\n„Während des Ausbruchs ist ein 12-Stunden-Tag in Ordnung.“" if de else
                  "\n\n“A 12-hour working day is fine during the outbreak.”")
    docs["doc-manager-email"].update(title="E-Mail Regionalleitung (weitergeleitet)" if de else "Email from the operations manager (forwarded)",
                                     body=email)
    memo = ("## Rundschreiben Personal (Ablage)\n\nÜbergangsregelung während der Umbauarbeiten im Erdgeschoss: Übergaben finden im "
            "Besprechungsraum im 1. OG statt. Dienstkleidung bitte weiterhin im Keller abholen." if de else
            "## Staffing memo (filed copy)\n\nInterim arrangement during the ground-floor works: handovers take place in the "
            "first-floor meeting room. Uniforms can still be collected from the basement store.")
    if f["memo_nights"] is not None:
        memo += (f"\n\nWenn der Wohnbereich voll belegt ist, sind bis zu {f['memo_nights']} Nächte am Stück erlaubt." if de else
                 f"\n\nWhen the unit is full, up to {f['memo_nights']} nights in a row are allowed.")
    if f["memo_deadline"]:
        memo += ("\n\nTauschwünsche bitte bis 12:00 am Antworttag beantworten." if de else
                 "\n\nPlease answer swap requests by 12:00 on the reply date.")
    docs["doc-expired-memo"].update(title="Altes Rundschreiben Personal" if de else "Old staffing memo", body=memo)
    if "doc-current-memo" in docs:
        docs["doc-current-memo"].update(
            title="Rundschreiben Dienstbetrieb, Fassung 2" if de else "Operations memo, revision 2",
            body=("## Rundschreiben Dienstbetrieb (Fassung 2)\n\nGilt ab sofort. Tauschwünsche bis 15:00 am angegebenen Antworttag "
                  "beantworten. Das ist nur eine Verwaltungsfrist; ein unzulässiger Dienst wird dadurch nicht erlaubt."
                  if de else "## Operations memo (revision 2)\n\nIn force now. Answer swap requests by 15:00 on the reply date given. "
                  "This is an admin deadline only; it does not make an unlawful shift allowed.")
            if f["current_memo_rev2"] else
            ("## Rundschreiben Dienstbetrieb\n\nBitte bei jeder Lücke Wohnbereich, Datum und Dienst angeben. Ein Rundschreiben hebt "
             "weder Gesetz noch Betriebsvereinbarung auf." if de else "## Operations memo\n\nPlease give the unit, date and shift when "
             "you report a gap. A memo cannot set aside the law or the signed agreement."))
    note_lines = [(f"{u}: use the qualifications shown in the rota demand; a team leader does not replace a registered nurse.",
                   f"{u}: nach dem Bedarf im Dienstplan planen; eine Schichtleitung ersetzt keine Pflegefachkraft.") for u in units]
    body = ("## Notizen der Wohnbereiche\n\n" if de else "## Unit notes\n\n") + "\n".join(f"- {x[pick]}" for x in note_lines)
    if f["dementia_note"]:
        body += ("\n\nÜbergabe Demenzbereich: in jedem Dienst zusätzlich eine Schichtleitung. Die Anzahl steht im Bedarf, nicht im Namen des Bereichs."
                 if de else "\n\nDementia unit handover: every shift also needs a team leader. The numbers are in the demand, not in the unit's name.")
    if f["reminders"]:
        body += ("\n\n### Hinweise zur Übergabe\n" if de else "\n\n### Handover reminders\n") + "\n".join(
            f"- {REMINDERS[r][pick]}" for r in f["reminders"])
    docs["doc-ward-notes"].update(title="Notizen der Wohnbereiche" if de else "Unit notes", body=body)
    world["documents"] = [docs[k] for k in sorted(docs)]


# ---------------------------------------------------------------------------------- messages

def _swap_text(locale: str, requester: str, own: dict, other: dict, partner: str) -> tuple[str, str]:
    d1, d2 = date.fromisoformat(own["date"]), date.fromisoformat(other["date"])
    s1, s2 = shift_name(locale, own["shift_type"]), shift_name(locale, other["shift_type"])
    if d1 == d2 and own["shift_type"] == other["shift_type"]:
        # Same shift, other unit: the two want to change units for the day.
        if locale == "de-DE":
            return ("Tauschwunsch", f"Hallo, {partner} und ich würden am {fmt_short(locale, d1)} im {s1}dienst gern die "
                    f"Wohnbereiche tauschen: ich nach {other['unit']}, {_first(partner)} zu mir. Geht das? "
                    f"Danke, {_first(requester)}")
        return ("Shift swap request", f"Hi, {partner} and I would like to swap units for the {s1.lower()} on "
                f"{fmt_short(locale, d1)}: me on {other['unit']}, {_first(partner)} on mine. Is that OK? "
                f"Thanks, {_first(requester)}")
    if locale == "de-DE":
        return ("Tauschwunsch", f"Hallo, könnte ich meinen {s1}dienst am {fmt_short(locale, d1)} mit dem {s2}dienst von "
                f"{partner} am {fmt_short(locale, d2)} tauschen? Wir haben schon gesprochen. Danke, {_first(requester)}")
    return ("Shift swap request", f"Hi, could I swap my {s1.lower()} on {fmt_short(locale, d1)} with {partner}'s "
            f"{s2.lower()} on {fmt_short(locale, d2)}? We've talked about it already. Thanks, {_first(requester)}")


def _sick_text(locale: str, name: str, start: date, end: date, today: date, taker: str, minute: int) -> tuple[str, str]:
    at = f"{minute // 60:02d}:{minute % 60:02d}"
    span_de = fmt_short(locale, start) if start == end else f"{fmt_short(locale, start)} bis {fmt_short(locale, end)}"
    span_en = fmt_short(locale, start) if start == end else f"{fmt_short(locale, start)} to {fmt_short(locale, end)}"
    if locale == "de-DE":
        if start == today:
            return (f"Krankmeldung {name}", f"{name} hat um {at} angerufen, ist krank und fällt aus ({span_de}). "
                    f"Im System eingetragen. Aufgenommen von {taker} (Nachtdienst).")
        return (f"Krankmeldung {name}", f"{name} hat um {at} angerufen: Krankschreibung liegt vor ({span_de}). "
                f"Im System eingetragen. Aufgenommen von {taker} (Nachtdienst).")
    if start == today:
        return (f"Sick call – {name}", f"{name} rang at {at}, not well and won't be in: {span_en}. Entered on the system. "
                f"Taken by {taker} (nights).")
    return (f"Sick call – {name}", f"{name} rang at {at}: signed off sick by the GP for {span_en}. Entered on the system. "
            f"Taken by {taker} (nights).")


def _slot_lines(locale: str, slots: list[tuple[date, str, str]]) -> list[str]:
    days = sorted({d for d, _, _ in slots})
    units = sorted({u for _, u, _ in slots})
    shifts = [s for s in ("early", "late", "night") if any(x == s for _, _, x in slots)]
    full = len(slots) == len(days) * len(units) * len(shifts) and len(shifts) == 3
    contiguous = all((b - a).days == 1 for a, b in zip(days, days[1:]))
    if len(slots) > 6 and full and contiguous:
        joined = (" und " if locale == "de-DE" else " and ").join(units)
        span = (f"{fmt_short(locale, days[0])} bis {fmt_short(locale, days[-1])}" if locale == "de-DE" else
                f"{fmt_short(locale, days[0])} to {fmt_short(locale, days[-1])}")
        return [f"{span}: {'alle Dienste' if locale == 'de-DE' else 'every shift'}, {joined}"]
    if locale == "de-DE":
        return [f"{fmt_short(locale, d)}, {u}, {shift_name(locale, x)}" for d, u, x in slots]
    return [f"{fmt_short(locale, d)}, {u}, {shift_name(locale, x).lower()}" for d, u, x in slots]


# why the clinical lead asks for more staff: one reason per request, so two requests never read the same (3.1.1)
DEMAND_REASONS = {
    "de-DE": (("Mehr Personal nötig", "Zwei Bewohner sind deutlich pflegeaufwändiger geworden."),
              ("Neuaufnahme – zusätzliche Besetzung", "Wir bekommen eine Neuaufnahme mit hohem Pflegebedarf."),
              ("Bitte mehr Personal (Palliativbegleitung)", "Eine Bewohnerin braucht ab sofort eine engere Begleitung.")),
    "en-GB": (("Extra staff needed", "Two residents need a lot more care since yesterday."),
              ("New admission – extra cover", "We have a new admission with high care needs."),
              ("Extra staff please (end-of-life care)", "One of our residents needs closer support from now on.")),
}


def _demand_text(locale: str, sender: str, slots: list[tuple[date, str, str]], extra: int, qual: str | None,
                 index: int = 0) -> tuple[str, str]:
    listed = "\n".join(f"- {line}" for line in _slot_lines(locale, slots))
    subject, why = DEMAND_REASONS["de-DE" if locale == "de-DE" else "en-GB"][index % 3]
    one = len(slots) == 1
    if locale == "de-DE":
        what = (f"{extra} × {qual_name(locale, qual)} zusätzlich" if qual else
                f"{extra} zusätzliche Person{'en' if extra > 1 else ''}")
        where = "in diesem Dienst" if one else "in diesen Diensten jeweils"
        return subject, f"{why} Wir brauchen {where} {what}:\n{listed}\nGruß, {sender}"
    what = f"{extra} extra {qual_name(locale, qual).lower() if qual else 'member of staff'}" + ("s" if extra > 1 and qual else "")
    where = "This shift needs" if one else "Each of these shifts needs"
    return subject, f"{why} {where} {what}:\n{listed}\nThanks, {sender}"


REASONS = {
    "The employee has this weekday fixed off.": ("That's my fixed day off, sorry.", "Das ist mein fester freier Tag, tut mir leid."),
    "The employee only accepts night shifts.": ("I only do nights.", "Ich arbeite nur nachts."),
    "The employee does not work Sundays.": ("I don't work Sundays.", "Sonntags arbeite ich nicht."),
    "The requested shift is not one of the employee's preferred shifts.": ("Sorry, that shift doesn't work for me.", "Tut mir leid, dieser Dienst passt bei mir nicht."),
    "The employee declines shift swaps.": ("I'd rather not swap, sorry.", "Ich möchte lieber nicht tauschen."),
    "The employee declines additional shifts.": ("I can't do extra shifts at the moment.", "Zusätzliche Dienste gehen gerade nicht."),
    "The employee is already rostered during that time.": ("I'm already working then.", "Da bin ich schon eingeteilt."),
    "The employee has an absence covering that shift.": ("I'm off then.", "Da bin ich nicht da."),
    "The requested shift is outside this episode.": ("That date isn't on the rota yet.", "Dieses Datum ist noch nicht im Plan."),
    "Employee not found.": ("Sorry, wrong person?", "Falsche Person?"),
}


RULE_REASONS = {  # first person, the way a colleague says it (3.1.1: never the rule's id)
    "daily_hours": ("That would be too many hours in one day for me.", "Das wären mir zu viele Stunden an einem Tag."),
    "daily_rest": ("I wouldn't get my rest between the shifts.", "Dann hätte ich nicht genug Ruhezeit zwischen den Diensten."),
    "weekly_hours": ("That would put me over my hours this week.", "Dann komme ich diese Woche über meine Stunden."),
    "part_time_hours": ("That would put me over my hours this week.", "Dann komme ich diese Woche über meine Stunden."),
    "max_consecutive_nights": ("That would be too many nights in a row.", "Das wären zu viele Nächte am Stück."),
    "max_consecutive_work_days": ("That would be too many days in a row for me.", "Das wären mir zu viele Tage am Stück."),
    "no_late_to_early": ("I can't do an early straight after a late.", "Frühdienst direkt nach einem Spätdienst geht nicht."),
    "monthly_night_limit": ("I've already got all my nights for this month.", "Meine Nächte für diesen Monat sind schon voll."),
    "free_sundays": ("I need to keep a Sunday free.", "Ich brauche noch einen freien Sonntag."),
    "night_worker_hours": ("That would be too much night work for me.", "Das wäre mir zu viel Nachtarbeit."),
    "weekly_rest": ("I wouldn't get my day off this week.", "Dann hätte ich diese Woche keinen freien Tag."),
    "rest_break": ("I wouldn't get a proper break.", "Dann hätte ich keine richtige Pause."),
    "employee_availability": ("I'm not available then.", "Da bin ich nicht da."),
}


def staff_reason(locale: str, reason: str) -> str:
    """A staff member's reason in their own words and the home's language – never an internal rule id."""
    pick = 1 if locale == "de-DE" else 0
    if reason in REASONS:
        return REASONS[reason][pick]
    m = re.fullmatch(r"The shift needs a (\w+) and the employee does not hold that qualification\.", reason)
    if m:
        what = qual_name(locale, m.group(1))
        return (f"Dafür bin ich nicht qualifiziert – der Dienst braucht eine {what}." if pick else
                f"I'm not qualified for that – the shift needs a {what.lower()}.")
    m = re.search(r"would violate (?:\w+\.)?(\w+)", reason)
    if m:
        return RULE_REASONS.get(m.group(1), ("That doesn't fit with the rules for me.", "Das passt mit den Regeln nicht."))[pick]
    return "Das geht leider nicht." if pick else "Sorry, I can't."


def _reply(locale: str, body: str, unit_names: dict[str, str]) -> str:
    pick = 1 if locale == "de-DE" else 0
    m = re.fullmatch(r"I accept the (\w+) shift on (\d{4}-\d{2}-\d{2})\.", body)
    if m:
        d = fmt_short(locale, date.fromisoformat(m.group(2)))
        s = shift_name(locale, m.group(1))
        return f"Ja, ich übernehme den {s}dienst am {d}." if pick else f"Yes, I can do the {s.lower()} on {d}."
    if body == "Message received.":
        return "Danke, habe ich gelesen." if pick else "Thanks, got it."
    m = re.fullmatch(r"I cannot accept that shift: (.*)", body, flags=re.S)
    if m:
        return staff_reason(locale, m.group(1))
    return body


def apply_runtime_texts(state: WorldState, app: WorkforceState) -> None:
    for message in state.messages:
        text = app.message_texts.get(message.message_id)
        if text:
            message.subject, message.body = text["subject"], text["body"]


def localize_new_messages(state: WorldState, app: WorkforceState, start: int) -> None:
    for message in state.messages[start:]:
        if message.recipient_id == "manager" and message.sender_id != "manager":
            message.body = _reply(app.locale, message.body, app.unit_names)


# ---------------------------------------------------------------------------------- bundle

def apply_veneer(task_dir: Path, *, salt: int) -> WorkforceState:
    """Rewrite task_dir/world.json in the home's language and write task_dir/app.json."""
    from opsdesk.core.world_loader import load_episode_state

    task = json.loads((task_dir / "task.json").read_text())
    world = json.loads((task_dir / "world.json").read_text())
    rng = random.Random(f"veneer:{task['task_id']}:{salt}")
    locale = LOCALES[world["jurisdiction"]]
    de = locale == "de-DE"
    start = date.fromisoformat(world["start_date"])

    people = _people(rng, locale, len(world["employees"]) + 4)
    staff_names = {e["employee_id"]: people[i] for i, e in enumerate(world["employees"])}
    manager, user, clinical = people[-1], people[-2], people[-3]
    for e in world["employees"]:
        e["name"] = staff_names[e["employee_id"]]
    home_name, address = _home(rng, locale)
    world["home_name"] = home_name
    world["home_id"] = "home-" + hashlib.sha256(f"home:{task['task_id']}".encode()).hexdigest()[:8]
    world["agency_budget"] = str(Decimal(str(world["agency_budget"])).quantize(Decimal("0.01")))
    if world["jurisdiction"] == "de":
        world["works_council_required"] = True  # a signed works agreement: rotas need the council's consent
    unit_pool = rng.sample(UNITS[locale], len(UNITS[locale]))
    units = []
    for ward in sorted(world["wards"], key=lambda w: w["ward_id"]):
        label = rng.choice(DEMENTIA_UNIT[locale]) if "dementia" in ward["name"].casefold() else unit_pool.pop(0)
        ward["name"] = label
        units.append(label)
    unit_names = {w["ward_id"]: w["name"] for w in world["wards"]}
    agency_ids = sorted({o["agency_id"] for o in world["agency_offers"]} | {r["agency_id"] for r in world["agency_requests"]})
    agency_names = {a: AGENCIES[locale][i % len(AGENCIES[locale])] for i, a in enumerate(agency_ids)}
    for item in world["agency_offers"] + world["agency_requests"]:
        if "agency_name" in item:
            item["agency_name"] = agency_names[item["agency_id"]]
    for doc in world.get("documents", []):  # 3.1.1: standing documents were not all written on the first day of the period
        if doc.get("effective_from") == world["start_date"] and not doc.get("effective_until"):
            doc["effective_from"] = (start - timedelta(days=rng.randint(45, 720))).isoformat()
    for entry in world.get("timeclock_entries", []):
        if entry.get("source") == "simulated":  # the core's default; a real time clock names its terminal
            entry["source"] = "terminal"
    _render_documents(world, locale, staff_names, units, task["family"])

    by_assignment = {a["assignment_id"]: a for a in world["roster"]["assignments"]}
    requests, message_meta = [], []
    for msg in world["messages"]:
        m = re.fullmatch(r"Could you exchange assignment (\S+) with assignment (\S+)\?", msg["body"])
        if m and msg["subject"] == "Shift swap request":
            first, second = by_assignment[m.group(1)], by_assignment[m.group(2)]
            requester = msg["sender_id"]
            own, other = (first, second) if first["employee_id"] == requester else (second, first)
            partner = other["employee_id"]
            msg["subject"], msg["body"] = _swap_text(locale, staff_names[requester], own,
                                                     {**other, "unit": unit_names[other["ward_id"]]}, staff_names[partner])
            requests.append(SwapRequest(request_id=f"request-{len(requests) + 1:04d}", message_id=msg["message_id"],
                                        requester_id=requester, partner_id=partner, first_assignment_id=m.group(1),
                                        second_assignment_id=m.group(2)))
            message_meta.append(MessageMeta(message_id=msg["message_id"], channel="app", sender_label=staff_names[requester]))
    night_ids = sorted(e["employee_id"] for e in world["employees"] if e.get("is_night_worker"))
    taker = staff_names[night_ids[0]] if night_ids else people[-4]
    slots_by_id = {x["slot_id"]: x for x in world["demand_slots"]}
    natural_ids = set()
    for ev in sorted(world["events"], key=lambda x: (x["occurs_on"], x["event_id"])):
        # Events the generator already applied to the start world get their inbox message here.
        message_id = f"message-event-{ev['event_id']}"
        if not ev.get("processed") or any(m["message_id"] == message_id for m in world["messages"]):
            continue
        payload = ev.get("payload", {})
        if ev["event_type"] == "sick_call" and payload.get("employee_id") in staff_names:
            first_day = date.fromisoformat(ev["occurs_on"])
            last_day = max(first_day, date.fromisoformat(str(payload.get("end_date", ev["occurs_on"]))))
            subject, body = _sick_text(locale, staff_names[payload["employee_id"]], first_day, last_day, start, taker,
                                       rng.randint(5 * 60 + 5, 6 * 60 + 25))
            sender, channel, label = payload["employee_id"], "phone", taker
        elif ev["event_type"] == "demand_spike":
            ids = payload.get("slot_ids") or [payload.get("slot_id")]
            hit = sorted((slots_by_id[i] for i in ids if i in slots_by_id),
                         key=lambda x: (x["date"], x["ward_id"], ("early", "late", "night").index(x["shift_type"])))
            slots = [(date.fromisoformat(x["date"]), unit_names[x["ward_id"]], x["shift_type"]) for x in hit]
            subject, body = _demand_text(locale, clinical, slots, int(payload.get("delta", 1)), payload.get("qualification"),
                                         sum(1 for m in message_meta if m.channel == "email"))
            sender, channel, label = "clinical-lead", "email", clinical
        else:
            continue
        world["messages"].append({"message_id": message_id, "sender_id": sender, "recipient_id": "manager",
                                  "subject": subject, "body": body, "sent_on": start.isoformat(), "is_read": False})
        message_meta.append(MessageMeta(message_id=message_id, channel=channel, sender_label=label))
        natural_ids.add(message_id)
    (task_dir / "world.json").write_text(json.dumps(world, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    announced = load_episode_state(task_dir, task)
    message_texts: dict[str, dict[str, str]] = {}
    initial_ids = {a["absence_id"] for a in world["absences"]}
    absence_meta = []
    for absence in sorted(announced.absences, key=lambda a: a.absence_id):
        planned = absence.absence_type.value != "sick" and not absence.absence_id.startswith("absence-event-")
        if absence.absence_id in initial_ids and planned:
            when = max(start - timedelta(days=rng.randint(10, 60)), absence.start_date - timedelta(days=90))
            training = absence.absence_id.startswith("training-")
            note = ("Pflichtfortbildung (Pflegefachkräfte)" if de else "Mandatory training (registered nurses)") \
                if training else ""
            absence_meta.append(AbsenceMeta(absence_id=absence.absence_id, entered_by=manager, entered_on=when,
                                            channel="planned", note=note))
        else:
            absence_meta.append(AbsenceMeta(absence_id=absence.absence_id, entered_by=taker, entered_on=start,
                                            channel="phone", note="Krankmeldung telefonisch" if de else "Rang in sick"))
    for msg in announced.messages:
        if msg.message_id in {r.message_id for r in requests} or msg.message_id in natural_ids:
            continue
        if msg.subject == "Sick call":
            event = next(e for e in announced.events if f"message-event-{e.event_id}" == msg.message_id)
            first_day = event.occurs_on
            last_day = max(first_day, date.fromisoformat(str(event.payload.get("end_date", first_day))))
            subject, body = _sick_text(locale, staff_names[msg.sender_id], first_day, last_day, start, taker,
                                       rng.randint(5 * 60 + 5, 6 * 60 + 25))
            message_texts[msg.message_id] = {"subject": subject, "body": body}
            message_meta.append(MessageMeta(message_id=msg.message_id, channel="phone", sender_label=taker))
        elif msg.subject == "Demand increase":
            m = re.search(r"needs (\d+) more staff(?: \((\w+)\))?: (.*)\.$", msg.body)
            slots = [(date.fromisoformat(x.split()[0]), unit_names[x.split()[1]], x.split()[2]) for x in m.group(3).split(", ")]
            subject, body = _demand_text(locale, clinical, slots, int(m.group(1)), m.group(2),
                                         sum(1 for x in message_meta if x.channel == "email"))
            message_texts[msg.message_id] = {"subject": subject, "body": body}
            message_meta.append(MessageMeta(message_id=msg.message_id, channel="email", sender_label=clinical))
        else:
            raise ValueError(f"veneer: no natural text for message {msg.message_id} ({msg.subject})")

    profiles = []
    certs = CERTS[locale]
    for e in announced.employees:
        persona = next((p for p in announced.staff_personas if p.employee_id == e.employee_id), None)
        wishes = []
        if persona is not None:
            for s in sorted(p.value for p in persona.preferred_shift_types):
                wishes.append(f"Möchte am liebsten {shift_name(locale, s)}dienste" if de else f"Prefers {shift_name(locale, s).lower()} shifts")
            if persona.never_sundays:
                wishes.append("Möchte sonntags nicht arbeiten" if de else "Would rather not work Sundays")
            if not persona.accepts_extra_shifts:
                wishes.append("Übernimmt derzeit keine Zusatzdienste" if de else "Not taking extra shifts at the moment")
            if not persona.accepts_swaps:
                wishes.append("Tauscht keine Dienste" if de else "Does not swap shifts")
        valid = {c: start + timedelta(days=rng.randint(60, 900)) for c in rng.sample(certs, 2)}
        if "registered_nurse" in {q.value for q in e.qualifications}:
            valid[RN_CERT[locale]] = start + timedelta(days=rng.randint(120, 1000))
        notes = ""
        roll = rng.random()
        if roll < 0.10:
            notes = "Bitte erst nach 10 Uhr anrufen." if de else "Please call after 10am if possible."
        elif roll < 0.18:
            notes = ("Stufenweise Wiedereingliederung nach Knie-OP (abgeschlossen 03/2025)." if de else
                     "Phased return after knee surgery (finished March 2025).")
        contract_min = int(e.contract.hours_per_week * 60 * 4.33)
        profiles.append(EmployeeProfile(
            employee_id=e.employee_id, staff_number=("PN-" if de else "E") + f"{2000 + int(e.employee_id[-4:]) * 7 % 7000:04d}",
            phone=(f"intern {200 + int(e.employee_id[-4:])}" if de else f"07700 900{rng.randint(0, 999):03d}"),
            hire_date=start - timedelta(days=rng.randint(200, 6000)), qualification_valid_until=valid, wishes=wishes,
            notes=notes, hours_balance_minutes=(worked := contract_min + rng.randint(-8, 12) * 30) - contract_min,
            previous_period_minutes=worked))  # the balance is last month's hours against the monthly contract
    for a, b in ((SIMILAR_FAMILY[locale][0], SIMILAR_FAMILY[locale][1]),):
        pair = [e for e in announced.employees if e.name.endswith(" " + a) or e.name.endswith(" " + b)]
        if len(pair) == 2:
            p = next(x for x in profiles if x.employee_id == pair[0].employee_id)
            p.notes = (p.notes + " " if p.notes else "") + (f"Nicht verwechseln mit {pair[1].name}." if de else
                                                            f"Not to be confused with {pair[1].name}.")

    history = []
    day = start - timedelta(days=28)
    for week in range(4):
        d = day + timedelta(days=7 * week)
        history.append(AuditEntry(at=f"{d.isoformat()} 16:{10 + week * 7:02d}", user=manager,
                                  action="Dienstplan veröffentlicht" if de else "Rota published",
                                  object=(f"Woche {d.isocalendar().week}" if de else f"Week {d.isocalendar().week}")))
    for absence in sorted(announced.absences, key=lambda a: a.absence_id):
        meta = next(m for m in absence_meta if m.absence_id == absence.absence_id)
        if meta.channel == "planned":
            history.append(AuditEntry(at=f"{meta.entered_on.isoformat()} 09:{rng.randint(10, 59):02d}", user=manager,
                                      action="Abwesenheit erfasst" if de else "Absence recorded",
                                      object=staff_names[absence.employee_id],
                                      after=f"{'Urlaub' if de else 'Holiday'} {fmt_date(locale, absence.start_date, weekday=False)}"
                                            f"–{fmt_date(locale, absence.end_date, weekday=False)}"))
    # earlier weeks' everyday changes by the people who use the system (3.1.1: varied, not one repeated edit)
    shifts = ("early", "late", "night")
    for _ in range(rng.randint(8, 14)):
        e, other = rng.sample(announced.employees, 2)
        d = start - timedelta(days=rng.randint(2, 27))
        at = f"{d.isoformat()} {rng.randint(7, 18):02d}:{rng.randint(0, 59):02d}"
        who, kind = rng.choice([(manager, "shift"), (user, "shift"), (user, "swap"), (taker, "sick"), (user, "contact"),
                                (clinical, "shift"), (manager, "agency"), (user, "notice")])
        name = staff_names[e.employee_id]
        if kind == "shift":
            before, after = rng.sample(shifts, 2)
            entry = AuditEntry(at=at, user=who, action="Dienst geändert" if de else "Shift changed", object=name,
                               before=f"{fmt_short(locale, d + timedelta(days=rng.randint(1, 6)))} {shift_name(locale, before)}",
                               after=shift_name(locale, after))
        elif kind == "swap":
            entry = AuditEntry(at=at, user=who, action="Tausch genehmigt" if de else "Swap approved",
                               object=f"{name} ↔ {staff_names[other.employee_id]}")
        elif kind == "sick":
            entry = AuditEntry(at=at, user=who, action="Krankmeldung erfasst" if de else "Sick leave recorded", object=name,
                               after=fmt_date(locale, d, weekday=False))
        elif kind == "contact":
            entry = AuditEntry(at=at, user=who, action="Kontaktdaten geändert" if de else "Contact details updated", object=name,
                               after="Telefonnummer" if de else "Phone number")
        elif kind == "agency":
            entry = AuditEntry(at=at, user=who, action="Zeitarbeit gebucht" if de else "Agency shift booked",
                               object=AGENCIES[locale][rng.randrange(len(AGENCIES[locale]))],
                               after=f"{fmt_short(locale, d + timedelta(days=rng.randint(1, 5)))} {shift_name(locale, rng.choice(shifts))}")
        else:
            entry = AuditEntry(at=at, user=who, action="Dienstmitteilung gesendet" if de else "Shift notice sent", object=name)
        history.append(entry)
    history.sort(key=lambda h: h.at)

    app = WorkforceState(
        locale=locale,
        home=HomeInfo(address=address, manager_name=manager, manager_title=TITLES[locale]["manager"], user_name=user,
                      user_title=TITLES[locale]["user"], night_lead_name=taker),
        unit_names=unit_names, profiles=profiles, absence_meta=absence_meta, message_meta=message_meta,
        message_texts=message_texts, requests=requests, history=history)
    from opsdesk.workforce.office import populate
    populate(app, world, task_id=task["task_id"], salt=salt, agency_names=list(AGENCIES[locale]),
             family=task["family"])  # 3.1 mail, chat, calendar
    (task_dir / APP_FILE).write_text(json.dumps(app.model_dump(mode="json"), indent=2, sort_keys=True,
                                                ensure_ascii=False) + "\n")
    return app
