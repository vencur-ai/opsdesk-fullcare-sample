"""House standards: the home's own thresholds, frequencies and measure catalogue, as configuration.

The German edition uses the head of nursing's answers (all 71 rows, `CONFIRMED` below; questions in
`docs/FRAGEN_PDL_DE.md`); the UK edition keeps the defaults until a UK professional has reviewed them. Each setting
carries its status in `HouseStandard.status`: "confirmed" once answered, otherwise "pdl-pending" with the question
number. Generation varies some values between homes (`vary`), so tasks test whether the
agent follows *this* home's rules. The values are design defaults, not facts about real homes.
"""
from __future__ import annotations
from typing import Literal
from pydantic import Field
from opsdesk.core.models import DomainModel, Qualification
PENDING = 'pdl-pending'

class SettingStatus(DomainModel):
    status: Literal['pdl-pending', 'confirmed'] = PENDING
    question: str

class Range(DomainModel):
    low: float | None = None
    high: float | None = None

    def outside(self, value: float) -> bool:
        return self.low is not None and value < self.low or (self.high is not None and value > self.high)

class InitialAssessment(DomainModel):
    scale_id: str
    within_hours: int

class Reposition(DomainModel):
    every_hours: int
    night_every_hours: int

class CatalogueEntry(DomainModel):
    """One kind of measure the home plans, with who may do it. `label` per locale."""
    key: str
    label: dict[str, str]
    duration_min: int
    qualification: Qualification
    skills: list[str] = Field(default_factory=list)

class PreventionRule(DomainModel):
    """A measure the home plans when a risk reaches one of `classes` ("always" rules have risk_kind "base").
    `per_day`: times a day (the nurse picks the times); `weekly`: once a week; `interval`: from `reposition`."""
    risk_kind: str
    classes: list[str] = Field(default_factory=list)
    catalogue_key: str
    kind: Literal['per_day', 'weekly', 'interval']
    per_day: int = 1
    weekdays: list[int] = Field(default_factory=lambda: [0])
    default_times: list[str] = Field(default_factory=list)

class HouseStandard(DomainModel):
    initial_assessments: list[InitialAssessment]
    reassess_every_days: dict[str, int]
    reassess_on: list[Literal['hospital_return', 'new_wound', 'fall', 'mobility_change', 'weight_loss', 'care_level_change']]
    reassess_event_only: list[str] = Field(default_factory=list)
    gp_round_minutes: int = 120
    gp_rounds_per_week: int = 2
    reposition: dict[str, Reposition]
    vital_ranges: dict[str, Range]
    weigh_every_days: int
    weight_loss_percent: float
    weight_loss_days: int
    fluid_target_ml: int
    fluid_days_below_inform_gp: int
    follow_up_minutes: int
    plan_window_minutes: int
    catalogue: list[CatalogueEntry]
    prevention: list[PreventionRule] = Field(default_factory=list)
    mattress_for_class: str = 'high'
    transfer_prints: list[str] = Field(default_factory=lambda: ['transfer_sheet', 'medication_plan'])
    return_assessments: list[str] = Field(default_factory=lambda: ['house_skin', 'house_falls'])
    lab_inform_gp: Literal['outside_normal_range', 'always', 'never'] = 'outside_normal_range'
    fluid_check_days: int = 3
    inspection_checklist: list[str] = Field(default_factory=lambda: ['orders_acknowledged', 'care_plan_review_in_future', 'assessments_not_overdue', 'pressure_risk_has_repositioning'])
    status: dict[str, SettingStatus]

    def mattress_needed(self, risk_class: str | None) -> bool:
        """A pressure-relief mattress from the house's level upwards (at risk < high)."""
        order = {'none': 0, 'at_risk': 1, 'high': 2}
        return risk_class is not None and order.get(risk_class, 0) >= order[self.mattress_for_class]

    def confirmed(self, key: str) -> bool:
        return key in self.status and self.status[key].status == 'confirmed'

    def entry(self, key: str) -> CatalogueEntry | None:
        return next((c for c in self.catalogue if c.key == key), None)

    def pending(self) -> list[str]:
        return sorted((k for k, s in self.status.items() if s.status == PENDING))

def _c(key: str, en: str, de: str, minutes: int, qual: Qualification, *skills: str) -> CatalogueEntry:
    return CatalogueEntry(key=key, label={'en-GB': en, 'de-DE': de}, duration_min=minutes, qualification=qual, skills=list(skills))
RN, CA = (Qualification.RN, Qualification.CA)
CATALOGUE = (_c('personal_care', 'Personal care', 'Grundpflege', 30, CA), _c('reposition', 'Reposition', 'Lagerung', 5, CA), _c('fluid_offer', 'Offer drinks', 'Getränke anbieten', 5, CA), _c('mobilisation', 'Mobilisation', 'Mobilisation', 15, CA), _c('vital_signs', 'Vital signs', 'Vitalzeichen messen', 10, CA), _c('weigh', 'Weigh', 'Wiegen', 10, CA), _c('glucose', 'Blood glucose', 'Blutzucker messen', 5, RN), _c('insulin', 'Insulin', 'Insulin verabreichen', 5, RN, 'insulin'), _c('wound_dressing', 'Wound dressing', 'Verbandwechsel', 20, RN, 'wound_care'), _c('catheter_care', 'Catheter care', 'Katheterpflege', 15, RN, 'catheter'), _c('compression', 'Compression stockings', 'Kompressionsstrümpfe', 10, CA), _c('round_escort', 'Accompany GP round', 'Arztvisite begleiten', 30, RN), _c('transport_ready', 'Get ready for transport', 'Für Transport vorbereiten', 20, CA))
PREVENTION = (PreventionRule(risk_kind='base', catalogue_key='personal_care', kind='per_day', per_day=1, default_times=['07:00']), PreventionRule(risk_kind='base', catalogue_key='vital_signs', kind='weekly', default_times=['09:00']), PreventionRule(risk_kind='pressure_ulcer', classes=['at_risk', 'high'], catalogue_key='reposition', kind='interval'), PreventionRule(risk_kind='falls', classes=['at_risk', 'high'], catalogue_key='mobilisation', kind='per_day', per_day=2, default_times=['10:00', '15:00']), PreventionRule(risk_kind='malnutrition', classes=['at_risk', 'high'], catalogue_key='fluid_offer', kind='per_day', per_day=5, default_times=['09:00', '11:00', '15:00', '17:00', '20:00']), PreventionRule(risk_kind='malnutrition', classes=['at_risk', 'high'], catalogue_key='weigh', kind='weekly', default_times=['07:30']))
UNITS = {'de': {'glucose': 'mg/dl'}, 'uk': {'glucose': 'mmol/L'}}
CONFIRMED: dict = {'H01': 8, 'H02': 2, 'H03': 2, 'H04': 2, 'H05': 2, 'H06': 'passt', 'H07': 'passt', 'H08': 1, 'H09': {'max_days': 365, 'on_event': True}, 'H10': {'max_days': 365, 'on_event': True}, 'H11': ['hospital_return', 'new_wound', 'fall', 'mobility_change', 'weight_loss', 'care_level_change'], 'H12': ['house_skin', 'house_falls', 'house_pain', 'house_nutrition', 'house_dependency'], 'H13': [4, 9], 'H14': [1, 4], 'H15': 'passt', 'H16': [2, 5], 'H17': [6, 10], 'H18': 'passt', 'H19': 'passt', 'H20': 'passt', 'H21': 'passt', 'H22': 'passt', 'H23': 'passt', 'H24': 3, 'H25': 8, 'H26': 'twice_weekly', 'H27': 'passt', 'H28': 'passt', 'H29': 'passt', 'H30': 'passt', 'H31': 'passt', 'H32': 'passt', 'H33': 'passt', 'H34': 'passt', 'H35': 30, 'H36': 'passt', 'H37': 'passt', 'H38': 30, 'H39': 'passt', 'H40': 1, 'H41': 5, 'H42': 'always', 'H43': 'passt', 'H44': 20, 'H45': 'passt', 'H46': 10, 'H47': 'passt', 'H48': 'passt', 'H49': 'passt', 'H50': 'passt', 'H51': {'qualification': 'registered_nurse', 'skills': []}, 'H52': 'passt', 'H53': {'qualification': 'registered_nurse', 'skills': []}, 'H54': 5, 'H55': 'passt', 'H56': 'passt', 'H57': {'qualification': 'registered_nurse', 'skills': []}, 'H58': 'passt', 'H59': 'passt', 'H60': 'passt', 'H61': 'passt', 'H62': 10, 'H63': 'passt', 'H64': 'passt', 'H65': {'qualification': 'registered_nurse', 'skills': []}, 'H66': 15, 'H67': 'passt', 'H68': 'passt', 'H69': {'qualification': 'registered_nurse', 'skills': []}, 'H70': ['transfer_sheet', 'medication_plan', 'care_report', 'daily_plan'], 'H71': 'passt'}
CONFIRMED_ON = '2026-11-01'

def default_standard(edition: Literal['de', 'uk'], answers: bool=True) -> HouseStandard:
    """The house standard of an edition; `answers=False` gives the German one without the head of nursing's answers."""
    glucose = Range(low=60, high=300) if edition == 'de' else Range(low=3.5, high=16.0)
    status = {key: SettingStatus(question=q) for key, q in (('initial_assessments', 'Q7'), ('reassess_every_days', 'Q9'), ('reassess_on', 'Q9'), ('reposition', 'Q14'), ('vital_ranges', 'Q11'), ('weigh_every_days', 'Q12'), ('weight_loss_percent', 'Q12'), ('weight_loss_days', 'Q12'), ('fluid_target_ml', 'Q13'), ('fluid_days_below_inform_gp', 'Q13'), ('follow_up_minutes', 'Q11'), ('plan_window_minutes', 'Q25'), ('catalogue', 'Q24'), ('scale_bands', 'Q10'), ('prevention', 'Q10'), ('mattress_for_class', 'Q10'), ('transfer_prints', 'Q31'), ('measure_durations', 'Q22'), ('return_assessments', 'Q32'), ('lab_inform_gp', 'Q21'), ('fluid_check_days', 'Q13'), ('inspection_checklist', 'Q34'))}
    std = HouseStandard(initial_assessments=[InitialAssessment(scale_id='house_skin', within_hours=24), InitialAssessment(scale_id='house_falls', within_hours=24), InitialAssessment(scale_id='house_pain', within_hours=24), InitialAssessment(scale_id='house_nutrition', within_hours=72), InitialAssessment(scale_id='house_dependency', within_hours=72)], reassess_every_days={'house_skin': 28, 'house_falls': 28, 'house_pain': 7, 'house_nutrition': 28, 'house_dependency': 90}, reassess_on=['hospital_return', 'new_wound', 'fall'], reposition={'at_risk': Reposition(every_hours=3, night_every_hours=4), 'high': Reposition(every_hours=2, night_every_hours=3)}, vital_ranges={'bp': Range(low=100, high=180), 'pulse': Range(low=50, high=110), 'temperature': Range(high=38.0), 'spo2': Range(low=90), 'glucose': glucose}, weigh_every_days=30, weight_loss_percent=5.0, weight_loss_days=90, fluid_target_ml=1500, fluid_days_below_inform_gp=2, follow_up_minutes=60, plan_window_minutes=30, catalogue=[c.model_copy(deep=True) for c in CATALOGUE], prevention=[r.model_copy(deep=True) for r in PREVENTION], status=status)
    if edition == 'de' and answers and CONFIRMED:
        from opsdesk.fullcare.pdl_rows import apply_confirmed
        apply_confirmed(std, CONFIRMED)
    return std
