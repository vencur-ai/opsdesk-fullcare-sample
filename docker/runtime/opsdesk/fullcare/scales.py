"""Generic assessment engine with pluggable item tables.

A scale is data: items, each with options worth points, and bands that turn the total into a risk class. The
engine checks the answers and computes total and class; nobody types a total. Only **our own house scales** are
registered: their items and wording are written for OpsDesk and copy no published scale. Published scales
(Braden, Norton, Barthel and others) are not built until their rights are checked (`docs/RIGHTS_CHECK_SCALES.md`);
a checked scale is added as one more `ScaleDef` with `owner` and `licence` filled in.

The bands (since 0.6.1) are the published scales' thresholds carried over to our house scales (head of nursing,
1.11.2026, rows H13–H17: Braden, falls screen, NRS/BESD, MNA, Barthel – named only, nothing of them copied). Higher totals mean more risk or
more need of help on every house scale.
"""

from __future__ import annotations

from typing import Literal

from opsdesk.core.models import DomainModel

RiskClass = Literal["none", "at_risk", "high"]


class ScaleOption(DomainModel):
    key: str
    label: dict[str, str]
    points: int


class ScaleItem(DomainModel):
    key: str
    label: dict[str, str]
    options: list[ScaleOption]


class Band(DomainModel):
    min_total: int
    risk_class: RiskClass


class ScaleDef(DomainModel):
    scale_id: str
    version: int
    title: dict[str, str]
    risk_kind: str  # which risk the class is about (RiskEntry.kind), or "dependency"
    owner: str = "OpsDesk house scale"
    licence: str = "own work"
    items: list[ScaleItem]
    bands: list[Band]  # ascending min_total; the highest band reached applies

    def item(self, key: str) -> ScaleItem | None:
        return next((i for i in self.items if i.key == key), None)

    @property
    def max_total(self) -> int:
        return sum(max(o.points for o in i.options) for i in self.items)


class ScaleError(ValueError):
    pass


def score(scale: ScaleDef, answers: dict[str, str]) -> tuple[int, RiskClass]:
    """Total and risk class for a complete set of answers (one option per item, nothing else)."""
    unknown = sorted(set(answers) - {i.key for i in scale.items})
    if unknown:
        raise ScaleError(f"Unknown item(s): {', '.join(unknown)}.")
    missing = [i.key for i in scale.items if i.key not in answers]
    if missing:
        raise ScaleError(f"Please answer every item (missing: {', '.join(missing)}).")
    total = 0
    for item in scale.items:
        option = next((o for o in item.options if o.key == answers[item.key]), None)
        if option is None:
            raise ScaleError(f"Not an option of item {item.key}: {answers[item.key]}.")
        total += option.points
    risk: RiskClass = "none"
    for band in sorted(scale.bands, key=lambda b: b.min_total):
        if total >= band.min_total:
            risk = band.risk_class
    return total, risk


def _o(key: str, points: int, en: str, de: str) -> ScaleOption:
    return ScaleOption(key=key, points=points, label={"en-GB": en, "de-DE": de})


def _i(key: str, en: str, de: str, *options: ScaleOption) -> ScaleItem:
    return ScaleItem(key=key, label={"en-GB": en, "de-DE": de}, options=list(options))


def _graded(key: str, en: str, de: str, grades: tuple[tuple[str, str], ...]) -> ScaleItem:
    """An item whose options score 0, 1, 2, … in the given order."""
    return _i(key, en, de, *(_o(f"g{n}", n, g_en, g_de) for n, (g_en, g_de) in enumerate(grades)))


NONE_SOME_MUCH = (("No", "Nein"), ("Partly", "Teilweise"), ("Yes", "Ja"))

HOUSE_SKIN = ScaleDef(
    scale_id="house_skin", version=1, risk_kind="pressure_ulcer",
    title={"en-GB": "Skin and pressure risk (house scale, modelled on Braden)", "de-DE": "Haut- und Druckrisiko (Hausskala, angelehnt an Braden)"},
    items=[
        _graded("moves_alone", "Changes position in bed without help", "Ändert die Lage im Bett ohne Hilfe",
                (("Always", "Immer"), ("Sometimes", "Manchmal"), ("Rarely", "Selten"), ("Never", "Nie"))),
        _graded("out_of_bed", "Time out of bed per day", "Zeit außerhalb des Bettes pro Tag",
                (("Most of the day", "Den größten Teil des Tages"), ("A few hours", "Einige Stunden"),
                 ("Under one hour", "Unter einer Stunde"), ("Bed-bound", "Bettlägerig"))),
        _graded("skin_moist", "Skin often damp (sweat, incontinence)", "Haut oft feucht (Schweiß, Inkontinenz)",
                NONE_SOME_MUCH),
        _graded("eats_less", "Eats less than half of meals", "Isst weniger als die Hälfte der Mahlzeiten",
                NONE_SOME_MUCH),
        _graded("feels_pressure", "Reports or shows discomfort from pressure",
                "Meldet oder zeigt Druckschmerz", (("Yes, reliably", "Ja, zuverlässig"), ("Unreliably", "Unzuverlässig"),
                                                    ("Not at all", "Gar nicht"))),
        _graded("skin_now", "Skin over bony areas today", "Haut über Knochenvorsprüngen heute",
                (("Intact", "Intakt"), ("Reddened, fades", "Gerötet, blasst ab"),
                 ("Reddened, does not fade", "Gerötet, blasst nicht ab"))),
    ],
    bands=[Band(min_total=0, risk_class="none"), Band(min_total=4, risk_class="at_risk"),
           Band(min_total=9, risk_class="high")])

HOUSE_FALLS = ScaleDef(
    scale_id="house_falls", version=1, risk_kind="falls",
    title={"en-GB": "Falls risk factors (house scale, modelled on a falls risk screen)", "de-DE": "Sturzrisikofaktoren (Hausskala, angelehnt an eine Sturzrisiko-Skala)"},
    items=[
        _graded("fell_recently", "Fell in the last 12 months", "In den letzten 12 Monaten gestürzt",
                (("No", "Nein"), ("Once", "Einmal"), ("More than once", "Mehrmals"))),
        _graded("walking", "Walking", "Gehen",
                (("Safe without aid", "Sicher ohne Hilfsmittel"), ("With aid, steady", "Mit Hilfsmittel, sicher"),
                 ("Unsteady", "Unsicher"))),
        _graded("orientation", "Knows own limits", "Kennt eigene Grenzen",
                (("Yes", "Ja"), ("Sometimes not", "Manchmal nicht"), ("No", "Nein"))),
        _graded("sight", "Sight (with glasses)", "Sehen (mit Brille)", (("Good", "Gut"), ("Poor", "Schlecht"))),
        _graded("night_toilet", "Gets up to the toilet at night", "Steht nachts zur Toilette auf",
                (("No", "Nein"), ("Yes", "Ja"))),
        _graded("dizzy", "Dizziness on standing up", "Schwindel beim Aufstehen", (("No", "Nein"), ("Yes", "Ja"))),
    ],
    bands=[Band(min_total=0, risk_class="none"), Band(min_total=1, risk_class="at_risk"),
           Band(min_total=4, risk_class="high")])

HOUSE_PAIN = ScaleDef(
    scale_id="house_pain", version=1, risk_kind="pain",
    title={"en-GB": "Pain check (house scale, modelled on NRS / BESD)", "de-DE": "Schmerzeinschätzung (Hausskala, angelehnt an NRS / BESD)"},
    items=[
        _graded("at_rest", "Pain at rest", "Schmerz in Ruhe",
                (("None", "Keiner"), ("Mild", "Leicht"), ("Moderate", "Mittel"), ("Severe", "Stark"))),
        _graded("on_moving", "Pain when moving or during care", "Schmerz bei Bewegung oder Pflege",
                (("None", "Keiner"), ("Mild", "Leicht"), ("Moderate", "Mittel"), ("Severe", "Stark"))),
        _graded("sleep", "Pain disturbs sleep", "Schmerz stört den Schlaf", (("No", "Nein"), ("Yes", "Ja"))),
        _graded("behaviour", "Behaviour changed (withdrawn, restless, guarding)",
                "Verhalten verändert (zurückgezogen, unruhig, schonend)", NONE_SOME_MUCH),
    ],
    bands=[Band(min_total=0, risk_class="none"), Band(min_total=3, risk_class="at_risk"),
           Band(min_total=6, risk_class="high")])

HOUSE_NUTRITION = ScaleDef(
    scale_id="house_nutrition", version=1, risk_kind="malnutrition",
    title={"en-GB": "Eating and drinking (house scale, modelled on MNA)", "de-DE": "Essen und Trinken (Hausskala, angelehnt an MNA)"},
    items=[
        _graded("lost_weight", "Clothes looser or weight lost recently", "Kleidung weiter oder Gewicht verloren",
                NONE_SOME_MUCH),
        _graded("meals", "Share of meals eaten", "Anteil der gegessenen Mahlzeiten",
                (("All or most", "Alles oder fast alles"), ("About half", "Etwa die Hälfte"),
                 ("A quarter or less", "Ein Viertel oder weniger"))),
        _graded("drinks", "Drinks without reminders", "Trinkt ohne Erinnerung",
                (("Yes", "Ja"), ("With reminders", "Mit Erinnerung"), ("Hardly at all", "Kaum"))),
        _graded("swallowing", "Coughs or chokes when swallowing", "Hustet oder verschluckt sich beim Schlucken",
                (("No", "Nein"), ("Sometimes", "Manchmal"), ("Often", "Oft"))),
        _graded("help_eating", "Needs help to eat", "Braucht Hilfe beim Essen",
                (("No", "Nein"), ("Some help", "Etwas Hilfe"), ("Full help", "Volle Hilfe"))),
    ],
    bands=[Band(min_total=0, risk_class="none"), Band(min_total=2, risk_class="at_risk"),
           Band(min_total=5, risk_class="high")])

HOUSE_DEPENDENCY = ScaleDef(
    scale_id="house_dependency", version=1, risk_kind="dependency",
    title={"en-GB": "Help needed in daily life (house scale, modelled on Barthel)", "de-DE": "Hilfebedarf im Alltag (Hausskala, angelehnt an Barthel)"},
    items=[_graded(key, en, de, (("Alone", "Selbstständig"), ("With some help", "Mit etwas Hilfe"),
                                 ("Fully by staff", "Vollständig durch Personal")))
           for key, en, de in (("washing", "Washing", "Waschen"), ("dressing", "Dressing", "Ankleiden"),
                               ("eating", "Eating", "Essen"), ("toilet", "Using the toilet", "Toilettengang"),
                               ("transfer", "Bed to chair", "Bett zum Stuhl"), ("walking", "Walking", "Gehen"),
                               ("continence", "Continence", "Kontinenz"))],
    bands=[Band(min_total=0, risk_class="none"), Band(min_total=6, risk_class="at_risk"),
           Band(min_total=10, risk_class="high")])

SCALES: dict[str, ScaleDef] = {s.scale_id: s for s in (HOUSE_SKIN, HOUSE_FALLS, HOUSE_PAIN, HOUSE_NUTRITION,
                                                       HOUSE_DEPENDENCY)}


def get_scale(scale_id: str) -> ScaleDef:
    scale = SCALES.get(scale_id)
    if scale is None:
        raise ScaleError(f"Unknown scale: {scale_id}.")
    return scale


def register(scale: ScaleDef) -> None:
    """Add a scale whose rights have been checked (owner and licence must say so)."""
    if scale.scale_id in SCALES:
        raise ScaleError(f"Scale already registered: {scale.scale_id}.")
    if scale.owner != "OpsDesk house scale" and scale.licence in ("", "own work"):
        raise ScaleError("A published scale needs its checked licence (docs/RIGHTS_CHECK_SCALES.md).")
    SCALES[scale.scale_id] = scale


def set_bands(scale_id: str, at_risk: int, high: int) -> None:
    """The house's thresholds for a scale (the head of nursing's answer, Q10)."""
    scale = get_scale(scale_id)
    for band in scale.bands:
        if band.risk_class == "at_risk":
            band.min_total = at_risk
        elif band.risk_class == "high":
            band.min_total = high


def _confirmed_bands() -> None:
    from opsdesk.fullcare.pdl_rows import rows
    from opsdesk.fullcare.standards import CONFIRMED
    for row in rows():
        value = CONFIRMED.get(row.id)
        if row.setting == "scale_bands" and value not in (None, "passt"):
            row.apply(None, tuple(value))


_confirmed_bands()
