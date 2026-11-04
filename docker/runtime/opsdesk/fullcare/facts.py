"""Required facts in free text (mails, notes): exact values, names, dates, times, words – never style.

A fact is a small JSON list, e.g. ["name", "Gertrud", "Maier"], ["dob", "1938-02-03"], ["value", "342"],
["time", "07:58"], ["word", "mattress", "matratze"]. `missing(text, facts)` returns the facts the text lacks.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december")
MONTHS_DE = ("januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november",
             "dezember")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold())


def date_forms(d: date) -> list[str]:
    return [d.isoformat(), f"{d.day:02d}.{d.month:02d}.{d.year}", f"{d.day}.{d.month}.{d.year}",
            f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}",
            f"{d.day} {MONTHS_EN[d.month - 1][:3]} {d.year}", f"{d.day}. {MONTHS_DE[d.month - 1]} {d.year}",
            f"{d.day:02d}.{d.month:02d}.{str(d.year)[2:]}"]


def _number(value: str) -> re.Pattern:
    whole, _, frac = value.partition(".")
    body = re.escape(whole) + (r"[.,]" + re.escape(frac) if frac else r"(?![.,]\d)")
    return re.compile(r"(?<![\d.,])" + body + r"(?!\d)")


def has(text: str, fact: list[str]) -> bool:
    low = _norm(text)
    kind, *args = fact
    if kind == "name":  # the person, in whole words (since 0.6.0; before: given and family name anywhere, as parts of words)
        return person(text, args[0], args[1], either_order=True, middle=True)
    if kind == "dob":
        return any(form.casefold() in low for form in date_forms(date.fromisoformat(args[0])))
    if kind == "date":
        d = date.fromisoformat(args[0])
        return any(form.casefold() in low for form in date_forms(d)) or bool(
            re.search(rf"(?<!\d){d.day:02d}\.{d.month:02d}\.?(?!\d)|(?<!\d){d.day}\.{d.month}\.(?!\d)", low))
    if kind == "value":
        return bool(_number(args[0]).search(low))
    if kind == "time":
        h, m = args[0].split(":")
        return bool(re.search(rf"(?<!\d)0?{int(h)}[:.]{m}(?!\d)", low))
    if kind == "word":
        return any(_word(a.casefold()).search(low) for a in args)
    raise ValueError(f"unknown fact kind {kind}")


SALUTATION = r"(frau|herr|herrn|mrs|mr|ms|miss)\.?"


def _word(needle: str) -> re.Pattern:
    """A required word in free text (since 0.6.0): a number in it keeps number boundaries ("10 mg" is not in "110 mg"),
    a word of up to three letters must stand alone ("hb" is not in "habe"); longer words may sit in a compound."""
    body = re.escape(needle)
    before = r"(?<![\d.,])" if needle[:1].isdigit() else (r"(?<!\w)" if len(needle) <= 3 else "")
    after = r"(?![\d])" if needle[-1:].isdigit() else (r"(?!\w)" if len(needle) <= 3 else "")
    return re.compile(before + body + after)


def person(text: str, given: str, family: str, either_order: bool = False, middle: bool = False,
           salutation: bool = True) -> bool:
    """A text names this person: the full name together ("Inge Koch") or a salutation with the family name ("Frau Koch"),
    as whole words ("kleine" is not Frau Klein, "Kochen" is not Inge Koch); with `either_order` also "Koch, Inge", with
    `middle` also a second given name in between ("Inge Maria Koch")."""
    low = _norm(text)
    g, f = re.escape(given.casefold()), re.escape(family.casefold())
    forms = ([rf"{g} {f}" if not middle else rf"{g} (\w+\.? )?{f}"] + ([rf"{SALUTATION} {f}"] if salutation else [])
             + ([rf"{f},? {g}"] if either_order else []))
    return bool(re.search(r"(?<!\w)(" + "|".join(forms) + r")(?!\w)", low))


def missing(text: str, facts: list[list[str]]) -> list[list[str]]:
    return [f for f in facts if not has(text, f)]


def fmt_value(v: float) -> str:
    """How a value appears in facts: no trailing .0, one decimal otherwise."""
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"
