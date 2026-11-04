"""Care-record objects of OpsDesk FullCare (`care.json`, part of the state hash).

Everything is fictional and generated from the seed. Doctors' orders, diagnoses and the medication plan are
read-only for the signed-in nurse: no tool writes them (`tools.py`), except the acknowledgement of an order.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from opsdesk.core.models import DomainModel, Qualification
from opsdesk.fullcare.standards import HouseStandard
from opsdesk.workforce.state import ChatMessage, MailMessage

CARE_SCHEMA = "fullcare-care/v1"

ResidentStatus = Literal["present", "in_hospital", "on_leave", "moved_out"]
ObservationKind = Literal["bp", "pulse", "temperature", "spo2", "glucose", "weight", "height", "respiration", "pain"]
Representation = Literal["none", "guardian", "power_of_attorney", "lpa_health"]


class Physician(DomainModel):
    physician_id: str
    name: str
    practice: str
    kind: Literal["gp", "specialist"]
    specialty: str = ""
    mail: str


class Resident(DomainModel):
    resident_id: str
    given_name: str
    family_name: str
    sex: Literal["f", "m"]
    birth_date: date
    birth_place: str
    ward_id: str
    room: str
    admitted_on: date
    status: ResidentStatus = "present"
    care_level: int | None = None  # DE Pflegegrad 1–5; UK homes leave it empty
    funding_note: str = ""  # UK
    gp_id: str
    religion: str = ""
    wishes: str = ""
    advance_directive: bool = False

    @property
    def name(self) -> str:
        return f"{self.given_name} {self.family_name}"


class ResidentContact(DomainModel):
    contact_id: str
    resident_id: str
    name: str
    relation: str
    phone: str
    mail: str = ""
    emergency: bool = False
    representation: Representation = "none"
    representation_scope: list[Literal["health", "residence", "finances"]] = Field(default_factory=list)
    information_allowed: bool = False


class Consent(DomainModel):
    consent_id: str
    resident_id: str
    kind: Literal["confidentiality_release", "photos", "pharmacy_data", "therapy_data"]
    contact_id: str | None = None  # release of confidentiality towards this contact
    granted_by: str
    granted_on: date
    valid_until: date | None = None


class Allergy(DomainModel):
    allergy_id: str
    resident_id: str
    substance: str
    reaction: str
    severity: Literal["mild", "moderate", "severe"]
    source: str  # the letter it came from
    entered_by: str
    entered_on: date


class Diagnosis(DomainModel):
    diagnosis_id: str
    resident_id: str
    icd10: str
    text: str
    kind: Literal["main", "secondary"]
    diagnosed_on: date
    entered_by: str  # a doctor


class Observation(DomainModel):
    """One measurement. `value2` is the diastolic value of a blood pressure. `source_ref` names where the value came
    from (a chat post, a mail, an attachment); the Phase 2 verifier checks entered values against it."""
    obs_id: str
    resident_id: str
    kind: ObservationKind
    value: float
    value2: float | None = None
    unit: str
    measured_at: str  # "YYYY-MM-DD HH:MM"
    measured_by: str
    method: str = ""
    source_ref: str | None = None
    note: str = ""
    entered_at: str
    entered_by: str


class FluidEntry(DomainModel):
    entry_id: str
    resident_id: str
    direction: Literal["in", "out"]
    kind: Literal["drink", "tube_feed", "infusion", "urine", "vomit", "drain"]
    ml: int
    at: str
    source_ref: str | None = None
    entered_by: str


class ExcretionEntry(DomainModel):
    entry_id: str
    resident_id: str
    kind: Literal["urine", "stool"]
    continence: Literal["continent", "incontinent", "catheter"]
    note: str = ""
    at: str
    source_ref: str | None = None
    entered_by: str


class AssessmentRecord(DomainModel):
    """A filled-in scale. Total and risk class are computed by `scales.score`, never typed."""
    assessment_id: str
    resident_id: str
    scale_id: str
    scale_version: int
    answers: dict[str, str]
    total: int
    risk_class: str
    performed_at: str
    performed_by: str
    next_due: date | None = None
    source_refs: list[str] = Field(default_factory=list)
    note: str = ""


class RiskEntry(DomainModel):
    risk_id: str
    resident_id: str
    kind: Literal["pressure_ulcer", "falls", "dehydration", "malnutrition", "contractures", "pain", "incontinence"]
    present: bool
    reason: str
    assessment_id: str | None = None
    since: date
    review_on: date | None = None


class CarePlanEntry(DomainModel):
    """One entry of the care plan. SIS homes fill `theme` and `finding`; problem/goal/measure homes fill `problem`,
    `resource` and `goal`. Which fields are required follows `CareState.careplan_model` (`careplan.py`)."""
    entry_id: str
    resident_id: str
    model: Literal["sis", "pgm"]
    theme: str = ""
    finding: str = ""
    problem: str = ""
    resource: str = ""
    goal: str = ""
    risk_kinds: list[str] = Field(default_factory=list)
    created_at: str
    created_by: str
    review_on: date
    status: Literal["active", "ended"] = "active"


class Schedule(DomainModel):
    """When a measure is due. `times`: fixed clock times every day; `interval`: every N hours from `anchor` (with a
    separate night interval); `weekly`: fixed times on given weekdays (0 = Monday)."""
    kind: Literal["times", "interval", "weekly"]
    times: list[str] = Field(default_factory=list)
    every_hours: int | None = None
    night_every_hours: int | None = None
    anchor: str = "06:00"
    weekdays: list[int] = Field(default_factory=list)


class Measure(DomainModel):
    measure_id: str
    resident_id: str
    entry_id: str | None = None  # care-plan entry it belongs to
    catalogue_key: str
    label: str
    schedule: Schedule
    duration_min: int
    qualification: Qualification
    skills: list[str] = Field(default_factory=list)
    start: date
    end: date | None = None
    source: Literal["care_plan", "order", "house_standard"]
    order_id: str | None = None
    note: str = ""
    created_by: str = ""


class CareReportEntry(DomainModel):
    entry_id: str
    resident_id: str
    at: str
    author: str
    category: Literal["general", "observation", "incident", "communication", "handover"]
    text: str
    observation_ids: list[str] = Field(default_factory=list)
    informed: list[str] = Field(default_factory=list)
    correction_of: str | None = None


class Order(DomainModel):
    """A doctor's order as the doctor entered it (or a transcribed phone order). Read-only except acknowledgement."""
    order_id: str
    resident_id: str
    physician_id: str
    kind: Literal["measurement", "measure", "wound_care", "medication", "referral", "diet", "restriction"]
    text: str
    catalogue_key: str = ""
    schedule: Schedule | None = None
    valid_from: date
    valid_to: date | None = None
    entered_at: str
    source: Literal["doctor_system", "transcribed"] = "doctor_system"
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None


class MedicationLine(DomainModel):
    """Read-only medication plan line (as ordered by the doctor). Doses per slot as written, e.g. "1" or "½"."""
    line_id: str
    resident_id: str
    drug: str
    strength: str
    form: str
    morning: str = "0"
    noon: str = "0"
    evening: str = "0"
    night: str = "0"
    prn: bool = False
    prn_max_per_day: str = ""
    indication: str = ""
    start: date
    end: date | None = None
    prescriber_id: str


class Appointment(DomainModel):
    appointment_id: str
    resident_id: str
    kind: Literal["gp_visit", "specialist", "therapy", "hospital", "relatives", "other"]
    date: date
    start: str
    end: str
    place: str
    with_whom: str = ""
    transport_needed: bool = False
    escort_qualification: Qualification | None = None
    status: Literal["planned", "cancelled", "done"] = "planned"
    created_by: str = ""


class PlanItemState(DomainModel):
    """What the nurse did with a generated daily-plan item (items themselves are computed, `dailyplan.py`)."""
    item_key: str
    assigned_to: str | None = None
    status: Literal["open", "done", "not_done", "handed_over"] = "open"
    reason: str = ""
    changed_at: str = ""
    by: str = ""  # who closed it: "" the signed-in nurse, else the colleague (employee id) who ticked it off


class ExtraPlanItem(DomainModel):
    """A one-off item added to a daily plan (e.g. a follow-up measurement)."""
    item_id: str
    resident_id: str
    date: date
    due: str
    label: str
    catalogue_key: str
    duration_min: int
    qualification: Qualification
    skills: list[str] = Field(default_factory=list)
    reason: str
    created_at: str
    created_by: str


class Wound(DomainModel):
    wound_id: str
    resident_id: str
    location: str  # body region in words, e.g. "sacrum", "left heel"
    kind: Literal["pressure_ulcer", "skin_tear", "leg_ulcer", "surgical", "other"]
    category: Literal["1", "2", "3", "4", "unstageable", ""] = ""  # pressure ulcers only
    first_seen: date
    origin: Literal["in_house", "brought_in"]
    status: Literal["open", "healed"] = "open"
    created_by: str


class WoundAssessment(DomainModel):
    wound_assessment_id: str
    wound_id: str
    resident_id: str
    at: str
    length_mm: int
    width_mm: int
    depth_mm: int
    exudate: Literal["none", "low", "moderate", "high"]
    wound_bed: str = ""
    dressing: str = ""
    next_change: date | None = None
    photo_taken: bool = False
    by: str


class CareDocument(DomainModel):
    """A letter, findings or lab report filed in the record (from a mail attachment). Shown as a PDF."""
    document_id: str
    resident_id: str
    kind: Literal["discharge_letter", "findings", "lab_report", "consent_form", "transport_form", "other"]
    title: str
    dated: date
    sender: str
    text: str
    source_attachment_id: str | None = None
    filed_at: str
    filed_by: str


class TransferSheet(DomainModel):
    """The hospital transfer sheet. Identity, allergies, diagnoses, medication and the last values are printed from the
    record; the nurse fills what only the people around the resident know."""
    sheet_id: str
    resident_id: str
    destination: str
    reason: str
    mobility: Literal["walks_alone", "walks_with_aid", "wheelchair", "bedbound"]
    continence: Literal["continent", "urine_incontinent", "double_incontinent", "catheter"]
    orientation: Literal["oriented", "partly_oriented", "disoriented"]
    aids_sent: list[Literal["glasses", "hearing_aid", "dentures", "walking_frame", "wheelchair"]] = Field(default_factory=list)
    isolation: bool = False
    contact_for_questions: str = ""
    saved_at: str
    saved_by: str


class PrintJob(DomainModel):
    job_id: str
    kind: Literal["transfer_sheet", "medication_plan", "daily_plan", "care_report", "document", "round_sheet"]
    resident_id: str | None = None
    ref: str = ""  # sheet id, document id, or "date/shift/employee" for a daily plan
    copies: int = 1
    printer: str
    submitted_at: str
    submitted_by: str
    status: Literal["queued", "printed", "cancelled"] = "printed"


class RoundPlan(DomainModel):
    """A doctor's round: who is seen, with which points, and which nurse goes with the doctor."""
    round_id: str
    physician_id: str
    date: date
    start: str
    escort_id: str
    points: dict[str, list[str]]  # resident id -> point codes
    created_at: str
    created_by: str


class Escalation(DomainModel):
    """A note to the head of nursing: what could not be done and why."""
    escalation_id: str
    resident_ids: list[str] = Field(default_factory=list)
    subject: str
    text: str
    at: str
    by: str


class Handover(DomainModel):
    """The written handover of one shift to the next (long tier): free text plus the residents it is about."""
    handover_id: str
    ward_id: str
    shift: Literal["early", "late", "night"]
    text: str
    resident_ids: list[str] = Field(default_factory=list)
    written_at: str
    by: str


class StagedArrival(DomainModel):
    """Something that reaches the workspace during the shift (long tier): mails and chat posts that appear at the
    first change at or after `at` (clock time). Hidden until then; `fired_at` records when it arrived."""
    staged_id: str
    at: str  # "HH:MM"
    mails: list[MailMessage] = Field(default_factory=list)
    chats: list[ChatMessage] = Field(default_factory=list)
    fired_at: str | None = None


class CareState(DomainModel):
    schema_version: Literal["fullcare-care/v1"] = CARE_SCHEMA
    edition: Literal["de", "uk"]
    careplan_model: Literal["sis", "pgm"]
    standard: HouseStandard
    physicians: list[Physician]
    residents: list[Resident]
    contacts: list[ResidentContact] = Field(default_factory=list)
    consents: list[Consent] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    fluids: list[FluidEntry] = Field(default_factory=list)
    excretions: list[ExcretionEntry] = Field(default_factory=list)
    assessments: list[AssessmentRecord] = Field(default_factory=list)
    risks: list[RiskEntry] = Field(default_factory=list)
    careplan: list[CarePlanEntry] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    reports: list[CareReportEntry] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    medication: list[MedicationLine] = Field(default_factory=list)
    appointments: list[Appointment] = Field(default_factory=list)
    plan_state: list[PlanItemState] = Field(default_factory=list)
    plan_extra: list[ExtraPlanItem] = Field(default_factory=list)
    staff_skills: dict[str, list[str]] = Field(default_factory=dict)
    wounds: list[Wound] = Field(default_factory=list)
    wound_assessments: list[WoundAssessment] = Field(default_factory=list)
    documents: list[CareDocument] = Field(default_factory=list)
    transfer_sheets: list[TransferSheet] = Field(default_factory=list)
    print_jobs: list[PrintJob] = Field(default_factory=list)
    rounds: list[RoundPlan] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    printers: list[str] = Field(default_factory=list)  # one per unit, e.g. "Printer Ahorn"
    # long tier ("run the early shift"): own clock start, arrivals during the shift, colleagues working their plan
    clock_start: int | None = None  # minute of the day at the first change (None: the Workforce default, 08:05)
    waited_min: int = 0  # minutes the nurse waited (`shift.wait`)
    staged: list[StagedArrival] = Field(default_factory=list)
    colleagues_work: bool = False  # colleagues tick off their own plan items when the clock passes them
    away: list[str] = Field(default_factory=list)  # staff who are really not at work (rang in sick), rota or not
    handovers: list[Handover] = Field(default_factory=list)

    def resident(self, resident_id: str) -> Resident | None:
        return next((r for r in self.residents if r.resident_id == resident_id), None)

    def physician(self, physician_id: str) -> Physician | None:
        return next((p for p in self.physicians if p.physician_id == physician_id), None)
