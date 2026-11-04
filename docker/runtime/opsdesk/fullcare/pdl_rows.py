"""The house-standard settings as rows for the head of nursing: one row per value she can confirm or change.

Each row has a stable number (H01 …), a topic, a plain German question, the question number of
`docs/FRAGEN_PDL_DE.md`, the setting it belongs to (`HouseStandard.status` key), how the current value is shown, how
an answer is read, and how it is applied. Used by the review sheet (`pdl_sheet.py`), the answer template
(`docs/ANTWORTEN_PDL_DE.md`), `scripts/apply_pdl_answers.py` and `standards.default_standard` (confirmed answers).

Answers apply to the German edition; the UK edition stays "pdl-pending" until a UK nurse has answered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from opsdesk.core.models import Qualification

SCALES = (("house_skin", "Haut und Druck"), ("house_falls", "Sturz"), ("house_pain", "Schmerz"),
          ("house_nutrition", "Essen und Trinken"), ("house_dependency", "Hilfebedarf im Alltag"))
SCALE_NAME = dict(SCALES)
TOPICS = {"A": "Einschätzungen", "B": "Vorbeugung (Lagerung, Bewegung, Trinken, Matratze)",
          "C": "Werte und Arzt-Information", "D": "Gewicht und Trinkmenge", "E": "Laborbefunde",
          "F": "Tagesplan: Dauer und wer was darf", "G": "Krankenhaus-Verlegung und Prüfung"}
REASSESS_ON = (("hospital_return", "Rückkehr aus dem Krankenhaus"), ("new_wound", "neue Wunde"), ("fall", "Sturz"),
               ("mobility_change", "veränderte Beweglichkeit"), ("weight_loss", "Gewichtsverlust über der Grenze"),
               ("care_level_change", "Änderung des Pflegegrads"))
PRINTS = (("transfer_sheet", "Überleitungsbogen"), ("medication_plan", "Medikationsplan"), ("care_report", "Pflegebericht"),
          ("daily_plan", "Tagesplan"))
CHECKS = (("orders_acknowledged", "Alle ärztlichen Anordnungen sind quittiert"),
          ("care_plan_review_in_future", "Kein Überprüfungsdatum der Pflegeplanung liegt in der Vergangenheit"),
          ("assessments_not_overdue", "Keine Einschätzung ist überfällig"),
          ("pressure_risk_has_repositioning", "Wer Dekubitusgefahr hat, hat eine Lagerung geplant"))
LAB = (("outside_normal_range", "nur bei Werten außerhalb des Normbereichs"), ("always", "bei jedem Befund"),
       ("never", "nie, der Arzt schaut selbst"))
CLASSES = (("at_risk", "schon ab „gefährdet“"), ("high", "erst ab „hohes Risiko“"))
OFTEN = (("weekly", "einmal pro Woche"), ("daily", "einmal täglich"), ("twice_weekly", "zweimal pro Woche (Mo und Fr)"))
QUAL = {Qualification.RN: "Pflegefachkraft", Qualification.CA: "Pflegehilfskraft"}
SKILL = {"insulin": "Insulin", "wound_care": "Wundversorgung", "catheter": "Katheter"}
VITAL = {"bp": "mmHg", "pulse": "/min", "temperature": "°C", "spo2": "%", "glucose": "mg/dl"}


@dataclass(frozen=True)
class Row:
    id: str
    topic: str
    question: str  # plain German
    q: str  # "Q11" -> docs/FRAGEN_PDL_DE.md, question 11
    setting: str  # HouseStandard.status key
    show: Callable[[Any], str]  # current value, in words
    parse: Callable[[str], Any]  # answer -> value (ValueError with a German message)
    apply: Callable[[Any, Any], None]  # (standard, value)
    form: str = ""  # how to write an answer, shown on the sheet

    @property
    def number(self) -> int:
        return int(self.q[1:])


# ---------------------------------------------------------------- reading answers

def _num(text: str) -> float:
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not m:
        raise ValueError("bitte eine Zahl")
    return float(m.group(0).replace(",", "."))


def _int(text: str) -> int:
    v = _num(text)
    if v != int(v) or v < 0:
        raise ValueError("bitte eine ganze Zahl")
    return int(v)


def _two(text: str) -> tuple[int, int]:
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) != 2:
        raise ValueError("bitte zwei Zahlen, z. B. 3 / 4")
    return nums[0], nums[1]


def _picks(options: tuple[tuple[str, str], ...], one: bool = False) -> Callable[[str], Any]:
    def parse(text: str):
        nums = [int(x) for x in re.findall(r"\d+", text)]
        if not nums or any(not 1 <= n <= len(options) for n in nums):
            raise ValueError(f"bitte Nummern von 1 bis {len(options)}")
        if one and len(nums) != 1:
            raise ValueError("bitte genau eine Nummer")
        keys = [options[n - 1][0] for n in sorted(set(nums))]
        return keys[0] if one else keys
    return parse


def _who(text: str) -> dict[str, Any]:
    low = text.casefold()
    if "fach" in low:
        qual = Qualification.RN
    elif "hilf" in low or "assist" in low:
        qual = Qualification.CA
    else:
        raise ValueError("bitte „Pflegefachkraft“ oder „Pflegehilfskraft“")
    skills = [k for k, word in (("insulin", "insulin"), ("wound_care", "wund"), ("catheter", "kathet")) if word in low]
    return {"qualification": qual, "skills": skills}


def _options_text(options: tuple[tuple[str, str], ...]) -> str:
    return " · ".join(f"{i}) {label}" for i, (_, label) in enumerate(options, 1))


def _chosen(options, keys) -> str:
    labels = dict(options)
    return ", ".join(labels[k] for k in keys) if keys else "keine"


def _times(n: int) -> list[str]:
    """n times a day, spread over the day (for planned measures)."""
    preset = {1: ["10:00"], 2: ["10:00", "15:00"], 3: ["10:00", "14:00", "17:00"], 4: ["09:00", "12:00", "15:00", "18:00"],
              5: ["09:00", "11:00", "15:00", "17:00", "20:00"], 6: ["08:00", "10:00", "12:00", "15:00", "17:00", "20:00"]}
    if n in preset:
        return preset[n]
    return [f"{8 + (12 * i) // max(n - 1, 1):02d}:00" for i in range(n)]


def _rule(std, key: str, risk: str | None = None):
    return next(r for r in std.prevention if r.catalogue_key == key and (risk is None or r.risk_kind == risk))


def _often_text(std, key: str) -> str:
    rule = _rule(std, key)
    if rule.kind != "weekly":
        return "einmal täglich"
    return "zweimal pro Woche (Mo und Fr)" if rule.weekdays == [0, 4] else "einmal pro Woche"


def _cycle(text: str):
    """Days, or (0.6.1) "bei Anlass, spätestens N Tage": repeated on an event, at the latest after N days."""
    if "anlass" in text.casefold() or "bedarf" in text.casefold():
        return {"on_event": True, "max_days": _int(text)}
    return _int(text)


def _set_cycle(std, sid: str, v) -> None:
    if isinstance(v, dict):
        std.reassess_every_days[sid] = int(v["max_days"])
        std.reassess_event_only = sorted(set(std.reassess_event_only) | {sid})
    else:
        std.reassess_every_days[sid] = v
        std.reassess_event_only = [x for x in std.reassess_event_only if x != sid]


def _cycle_text(std, sid: str) -> str:
    days = std.reassess_every_days[sid]
    return f"bei Anlass, spätestens alle {days} Tage" if sid in std.reassess_event_only else f"alle {days} Tage"


def _set_often(std, key: str, value: str) -> None:
    rule = _rule(std, key)
    if value == "twice_weekly":
        rule.kind, rule.per_day, rule.weekdays = "weekly", 1, [0, 4]
    elif value == "weekly":
        rule.kind, rule.per_day, rule.weekdays = "weekly", 1, [0]
    else:
        rule.kind, rule.per_day = "per_day", 1
    rule.default_times = rule.default_times[:1] or ["09:00"]


# ---------------------------------------------------------------- the rows

def _rows() -> list[Row]:
    rows: list[Row] = []

    def add(topic, question, q, setting, show, parse, apply, form=""):
        rows.append(Row(f"H{len(rows) + 1:02d}", topic, question, q, setting, show, parse, apply, form))

    for sid, name in SCALES:
        add("A", f"Einschätzung „{name}“ beim Einzug: Innerhalb wie vieler Stunden muss sie fertig sein?", "Q7",
            "initial_assessments",
            lambda s, sid=sid: f"{next(a.within_hours for a in s.initial_assessments if a.scale_id == sid)} Stunden",
            _int, lambda s, v, sid=sid: setattr(next(a for a in s.initial_assessments if a.scale_id == sid), "within_hours", v),
            "Stunden, z. B. 24")
    for sid, name in SCALES:
        add("A", f"Einschätzung „{name}“: Nach wie vielen Tagen wird sie regelmäßig wiederholt?", "Q9", "reassess_every_days",
            lambda s, sid=sid: _cycle_text(s, sid), _cycle,
            lambda s, v, sid=sid: _set_cycle(s, sid, v), "Tage, z. B. 28 – oder „bei Anlass, spätestens 365 Tage“")
    add("A", "Bei welchen Ereignissen wiederholen Sie Einschätzungen sofort? " + _options_text(REASSESS_ON), "Q9", "reassess_on",
        lambda s: _chosen(REASSESS_ON, s.reassess_on), _picks(REASSESS_ON), lambda s, v: setattr(s, "reassess_on", v),
        "Nummern, z. B. 1, 3")
    scale_opts = tuple(SCALES)
    add("A", "Nach einem Krankenhausaufenthalt: Welche Einschätzungen machen Sie neu? " + _options_text(scale_opts), "Q32",
        "return_assessments", lambda s: _chosen(scale_opts, s.return_assessments), _picks(scale_opts),
        lambda s, v: setattr(s, "return_assessments", v), "Nummern, z. B. 1, 2")
    for sid, name in SCALES:
        add("A", f"Einschätzung „{name}“: Ab wie vielen Punkten gilt jemand als „gefährdet“, ab wie vielen als „hohes Risiko“?",
            "Q10", "scale_bands", lambda s, sid=sid: "{} / {} Punkte".format(*_bands(s, sid)), _two,
            lambda s, v, sid=sid: _set_bands(s, sid, v), "zwei Zahlen, z. B. 5 / 9")

    for cls, name in (("at_risk", "gefährdet"), ("high", "hohes Risiko")):
        add("B", f"Lagerung bei Dekubitusgefahr „{name}“: tagsüber alle wie viele Stunden, nachts alle wie viele Stunden?", "Q14",
            "reposition", lambda s, cls=cls: f"tagsüber alle {s.reposition[cls].every_hours} Std., nachts alle "
                                             f"{s.reposition[cls].night_every_hours} Std.",
            _two, lambda s, v, cls=cls: _set_reposition(s, cls, v), "tagsüber / nachts, z. B. 3 / 4")
    add("B", "Ab welcher Stufe planen Sie Vorbeugung (Lagerung, Bewegung, Getränke)? " + _options_text(CLASSES), "Q10", "prevention",
        lambda s: dict(CLASSES)[_rule(s, "reposition").classes[0]], _picks(CLASSES, one=True), _set_classes, "eine Nummer")
    add("B", "Ab welcher Stufe bestellen Sie eine Wechseldruckmatratze? " + _options_text(CLASSES), "Q10", "mattress_for_class",
        lambda s: dict(CLASSES)[s.mattress_for_class], _picks(CLASSES, one=True), lambda s, v: setattr(s, "mattress_for_class", v),
        "eine Nummer")
    add("B", "Grundpflege: Wie oft am Tag wird sie geplant?", "Q10", "prevention",
        lambda s: f"{_rule(s, 'personal_care').per_day}-mal", _int, lambda s, v: _set_per_day(s, "personal_care", v), "Anzahl")
    add("B", "Vitalzeichen ohne besonderen Anlass: Wie oft messen Sie? " + _options_text(OFTEN), "Q10", "prevention",
        lambda s: _often_text(s, "vital_signs"),
        _picks(OFTEN, one=True), lambda s, v: _set_often(s, "vital_signs", v), "eine Nummer")
    add("B", "Bei Sturzgefahr: Wie oft am Tag wird Mobilisation geplant?", "Q10", "prevention",
        lambda s: f"{_rule(s, 'mobilisation').per_day}-mal", _int, lambda s, v: _set_per_day(s, "mobilisation", v), "Anzahl")
    add("B", "Bei Gefahr der Mangelernährung: Wie oft am Tag werden Getränke angeboten (eigene Runde)?", "Q10", "prevention",
        lambda s: f"{_rule(s, 'fluid_offer').per_day}-mal", _int, lambda s, v: _set_per_day(s, "fluid_offer", v), "Anzahl")
    add("B", "Bei Gefahr der Mangelernährung: Wie oft wird gewogen? " + _options_text(OFTEN), "Q10", "prevention",
        lambda s: _often_text(s, "weigh"),
        _picks(OFTEN, one=True), lambda s, v: _set_often(s, "weigh", v), "eine Nummer")

    for kind, side, text in (("bp", "high", "Blutdruck (oberer Wert): Ab welchem Wert informieren Sie den Hausarzt?"),
                             ("bp", "low", "Blutdruck (oberer Wert): Unter welchem Wert informieren Sie den Hausarzt?"),
                             ("pulse", "high", "Puls: Ab welchem Wert informieren Sie den Hausarzt?"),
                             ("pulse", "low", "Puls: Unter welchem Wert informieren Sie den Hausarzt?"),
                             ("temperature", "high", "Temperatur: Ab welchem Wert informieren Sie den Hausarzt?"),
                             ("spo2", "low", "Sauerstoffsättigung: Unter welchem Wert informieren Sie den Hausarzt?"),
                             ("glucose", "high", "Blutzucker: Ab welchem Wert informieren Sie den Hausarzt?"),
                             ("glucose", "low", "Blutzucker: Unter welchem Wert informieren Sie den Hausarzt?")):
        add("C", text, "Q11", "vital_ranges",
            lambda s, kind=kind, side=side: f"{_fmt(getattr(s.vital_ranges[kind], side))} {VITAL[kind]}", _num,
            lambda s, v, kind=kind, side=side: setattr(s.vital_ranges[kind], side, v), f"Zahl in {VITAL[kind]}")
    add("C", "Nach einem auffälligen Wert: Nach wie vielen Minuten wird nachgemessen?", "Q11", "follow_up_minutes",
        lambda s: f"nach {s.follow_up_minutes} Minuten", _int, lambda s, v: setattr(s, "follow_up_minutes", v), "Minuten")

    add("D", "Wie oft wird routinemäßig gewogen?", "Q12", "weigh_every_days", lambda s: f"alle {s.weigh_every_days} Tage", _int,
        lambda s, v: setattr(s, "weigh_every_days", v), "Tage")
    add("D", "Gewichtsverlust: Ab wie viel Prozent handeln Sie?", "Q12", "weight_loss_percent",
        lambda s: f"{_fmt(s.weight_loss_percent)} %", _num, lambda s, v: setattr(s, "weight_loss_percent", v), "Prozent")
    add("D", "Gewichtsverlust: In welchem Zeitraum gerechnet?", "Q12", "weight_loss_days", lambda s: f"{s.weight_loss_days} Tage",
        _int, lambda s, v: setattr(s, "weight_loss_days", v), "Tage")
    add("D", "Trinkmenge: Wie viel Milliliter am Tag ist das Ziel?", "Q13", "fluid_target_ml", lambda s: f"{s.fluid_target_ml} ml",
        _int, lambda s, v: setattr(s, "fluid_target_ml", v), "Milliliter")
    add("D", "Trinkmenge: Nach wie vielen Tagen in Folge unter dem Ziel informieren Sie den Hausarzt?", "Q13",
        "fluid_days_below_inform_gp", lambda s: f"nach {s.fluid_days_below_inform_gp} Tagen", _int,
        lambda s, v: setattr(s, "fluid_days_below_inform_gp", v), "Tage")
    add("D", "Trinkbilanz: Wie viele Tage schauen Sie zurück?", "Q13", "fluid_check_days", lambda s: f"{s.fluid_check_days} Tage",
        _int, lambda s, v: setattr(s, "fluid_check_days", v), "Tage")

    add("E", "Laborbefund: Wann geben Sie ihn an den Hausarzt? " + _options_text(LAB), "Q21", "lab_inform_gp",
        lambda s: dict(LAB)[s.lab_inform_gp], _picks(LAB, one=True), lambda s, v: setattr(s, "lab_inform_gp", v), "eine Nummer")

    add("F", "Tagesplan: Bis wie viele Minuten vor oder nach der geplanten Zeit gilt eine Aufgabe als pünktlich?", "Q25",
        "plan_window_minutes", lambda s: f"± {s.plan_window_minutes} Minuten", _int,
        lambda s, v: setattr(s, "plan_window_minutes", v), "Minuten")
    from opsdesk.fullcare.standards import CATALOGUE
    for entry in CATALOGUE:
        key, label = entry.key, entry.label["de-DE"]
        add("F", f"„{label}“: Wie viele Minuten planen Sie dafür?", "Q22", "measure_durations",
            lambda s, key=key: f"{s.entry(key).duration_min} Minuten", _int,
            lambda s, v, key=key: setattr(s.entry(key), "duration_min", v), "Minuten")
        add("F", f"„{label}“: Wer darf es machen? Braucht es eine Zusatzqualifikation?", "Q24", "catalogue",
            lambda s, key=key: _who_text(s.entry(key)), _who, lambda s, v, key=key: _set_who(s, key, v),
            "Pflegefachkraft oder Pflegehilfskraft, ggf. „+ Insulin / Wundversorgung / Katheter“")

    add("G", "Verlegung ins Krankenhaus: Was wird ausgedruckt und mitgegeben? " + _options_text(PRINTS), "Q31", "transfer_prints",
        lambda s: _chosen(PRINTS, s.transfer_prints), _picks(PRINTS), lambda s, v: setattr(s, "transfer_prints", v),
        "Nummern, z. B. 1, 2")
    add("G", "Vor einer Prüfung: Was muss in Ordnung sein? " + _options_text(CHECKS), "Q34", "inspection_checklist",
        lambda s: ", ".join(str(i) for i, (k, _) in enumerate(CHECKS, 1) if k in s.inspection_checklist),
        _picks(CHECKS), lambda s, v: setattr(s, "inspection_checklist", v), "Nummern, z. B. 1, 2, 3, 4")
    return rows


def _fmt(v: float | None) -> str:
    if v is None:
        return "–"
    return (str(int(v)) if float(v).is_integer() else f"{v:.1f}").replace(".", ",")


def _bands(std, sid: str) -> tuple[int, int]:
    """The scale's bands (the house scales are ours and the same in both editions, so bands live with the scale)."""
    from opsdesk.fullcare import scales
    bands = {b.risk_class: b.min_total for b in scales.get_scale(sid).bands}
    return bands["at_risk"], bands["high"]


def _set_bands(std, sid: str, v: tuple[int, int]) -> None:
    from opsdesk.fullcare import scales
    if not 0 < v[0] < v[1]:
        raise ValueError("die erste Zahl muss kleiner sein als die zweite")
    scales.set_bands(sid, v[0], v[1])


def _set_reposition(std, cls: str, v: tuple[int, int]) -> None:
    std.reposition[cls].every_hours, std.reposition[cls].night_every_hours = v


def _set_classes(std, value: str) -> None:
    classes = ["at_risk", "high"] if value == "at_risk" else ["high"]
    for rule in std.prevention:
        if rule.risk_kind != "base":
            rule.classes = list(classes)


def _set_per_day(std, key: str, n: int) -> None:
    if n < 1:
        raise ValueError("mindestens 1")
    rule = _rule(std, key)
    rule.kind, rule.per_day, rule.default_times = "per_day", n, _times(n)


def _who_text(entry) -> str:
    return QUAL.get(entry.qualification, entry.qualification.value) + "".join(f" + {SKILL[k]}" for k in entry.skills)


def _set_who(std, key: str, v: dict[str, Any]) -> None:
    entry = std.entry(key)
    entry.qualification, entry.skills = v["qualification"], v["skills"]


ROWS: tuple[Row, ...] = ()


def rows() -> tuple[Row, ...]:
    global ROWS
    if not ROWS:
        ROWS = tuple(_rows())
    return ROWS


def by_id() -> dict[str, Row]:
    return {r.id: r for r in rows()}


def apply_confirmed(std, confirmed: dict[str, Any]) -> None:
    """Apply the head of nursing's answers (row id -> parsed value, or "passt") to a German standard and mark each
    setting "confirmed" once every row of it is answered."""
    table = by_id()
    for rid, value in confirmed.items():
        if value != "passt":
            table[rid].apply(std, _restore(value))
    answered = set(confirmed)
    for setting, status in std.status.items():
        own = [r.id for r in rows() if r.setting == setting]
        if own and all(r in answered for r in own):
            status.status = "confirmed"


def _restore(value: Any) -> Any:
    """Values come back from the file as JSON: qualifications as strings, pairs as lists."""
    if isinstance(value, dict) and "qualification" in value:
        return {"qualification": Qualification(value["qualification"]), "skills": list(value["skills"])}
    if isinstance(value, list) and len(value) == 2 and all(isinstance(x, int) for x in value):
        return tuple(value)
    return value
