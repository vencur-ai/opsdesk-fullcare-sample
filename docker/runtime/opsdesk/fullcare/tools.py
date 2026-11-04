"""FullCare tools: the care record and the daily plan, one `AppTool` per action (HTTP, MCP and GUI alike).

The care state is bound for the call, as Workforce binds its app state. There is deliberately **no** tool that
creates or changes a doctor's order, a diagnosis or a medication line: the nurse can only acknowledge an order.
Measures that give medicine (catalogue key "insulin") need a medication order to point to.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from opsdesk.apps.base import AppTool, ToolArgs, ToolError
from opsdesk.apps.ids import next_monotonic_id
from opsdesk.core.models import Qualification
from opsdesk.core.state import WorldState
from opsdesk.fullcare import careplan, dailyplan, scales
from opsdesk.fullcare.models import (
    Allergy, AssessmentRecord, Appointment, CareDocument, CarePlanEntry, CareReportEntry, CareState, Consent,
    Escalation, ExcretionEntry, ExtraPlanItem, FluidEntry, Handover, Measure, Observation, PrintJob, Resident, ResidentContact,
    RiskEntry, RoundPlan, Schedule, TransferSheet, Wound, WoundAssessment,
)
from opsdesk.workforce.state import WorkforceState

Day = date  # for argument fields called "date"


@dataclass
class CareContext:
    care: CareState
    app: WorkforceState


_CURRENT: contextvars.ContextVar[CareContext | None] = contextvars.ContextVar("care_context", default=None)


def bind(ctx: CareContext):
    return _CURRENT.set(ctx)


def unbind(token) -> None:
    _CURRENT.reset(token)


def _ctx() -> CareContext:
    ctx = _CURRENT.get()
    if ctx is None:
        raise ToolError("This function is not available here.", "UNKNOWN_TOOL")
    return ctx


def _now(state: WorldState) -> str:
    return _ctx().app.now(state.current_date)


def _user() -> str:
    return _ctx().app.home.user_name


def _resident(resident_id: str, *, present: bool = False):
    r = _ctx().care.resident(resident_id)
    if r is None:
        raise ToolError("Resident not found.", "NOT_FOUND")
    if present and r.status != "present":
        raise ToolError("The resident is not in the home at the moment.")
    return r


def _when(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ToolError("Please give date and time as YYYY-MM-DD HH:MM.", "INVALID_ARGUMENTS") from exc


def _past(state: WorldState, at: str, *, max_days: int = 7) -> str:
    """A documented moment must be now or earlier, and not older than a week (late entries are noted as such)."""
    moment, now = _when(at), _when(_now(state))
    if moment > now:
        raise ToolError("This time is in the future.")
    if moment < now - timedelta(days=max_days):
        raise ToolError("Entries older than seven days need the head of nursing.")
    return moment.strftime("%Y-%m-%d %H:%M")


def _next_id(prefix: str, ids: list[str]) -> str:
    return next_monotonic_id(prefix, ids)


def _page(items: list[Any], limit: int, offset: int) -> dict[str, Any]:
    return {"total": len(items), "offset": offset, "items": items[offset:offset + limit]}


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
REASON = "Please give the reason in a few words."


def _meaningful(text: str) -> bool:
    return len(re.findall(r"[^\W\d_]", text)) >= 8 and len(text.split()) >= 2


# ---------------------------------------------------------------- read tools

class ResidentsArgs(ToolArgs):
    ward_id: str | None = None
    q: str | None = Field(default=None, description="Exact name part (case does not matter).")
    status: Literal["present", "in_hospital", "on_leave", "moved_out", "all"] = "present"
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ResidentsTool(AppTool):
    app = "care"
    name = "care.residents"
    description = ("List residents (room, name, age, unit, status, care level) with open points: orders to "
                   "acknowledge and assessments due. Search by an exact part of the name.")
    args_model = ResidentsArgs

    def execute(self, state: WorldState, args: ResidentsArgs) -> dict[str, Any]:
        care, today = _ctx().care, state.current_date
        out = []
        for r in sorted(care.residents, key=lambda x: (x.ward_id, x.room)):
            if args.ward_id and r.ward_id != args.ward_id or args.status != "all" and r.status != args.status:
                continue
            if args.q and args.q.lower() not in r.name.lower():
                continue
            due = sorted({a.scale_id for a in latest_assessments(care, r.resident_id).values()
                          if a.next_due is not None and a.next_due <= today})
            out.append({"resident_id": r.resident_id, "room": r.room, "name": r.name, "ward_id": r.ward_id,
                        "age": age(r.birth_date, today), "status": r.status, "care_level": r.care_level,
                        "orders_to_acknowledge": sum(1 for o in care.orders if o.resident_id == r.resident_id
                                                     and o.acknowledged_by is None),
                        "assessments_due": due})
        return _page(out, args.limit, args.offset)


SECTIONS = ("overview", "contacts", "diagnoses", "allergies", "vitals", "fluids", "excretion", "assessments", "risks",
            "careplan", "measures", "report", "orders", "medication", "appointments", "wounds", "documents", "transfer")


class RecordArgs(ToolArgs):
    resident_id: str
    section: Literal["overview", "contacts", "diagnoses", "allergies", "vitals", "fluids", "excretion", "assessments",
                     "risks", "careplan", "measures", "report", "orders", "medication", "appointments", "wounds",
                     "documents", "transfer"] = "overview"
    kind: str | None = Field(default=None, description="vitals only: one observation kind (bp, pulse, glucose, …).")
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class RecordTool(AppTool):
    app = "care"
    name = "care.record"
    description = ("Read one section of a resident's record: overview, contacts (with consents and representation), "
                   "diagnoses, allergies, vitals, fluids, excretion, assessments, risks, careplan, measures, report, "
                   "orders, medication (read-only plan), appointments, wounds (with assessments), documents (letters and "
                   "findings with their text) or transfer (saved transfer sheets). Lists are newest first and paginated.")
    args_model = RecordArgs

    def execute(self, state: WorldState, args: RecordArgs) -> dict[str, Any]:
        care = _ctx().care
        r = _resident(args.resident_id)
        rid = r.resident_id
        mine = lambda rows: [x.model_dump(mode="json") for x in rows if x.resident_id == rid]  # noqa: E731
        if args.section == "overview":
            gp = care.physician(r.gp_id)
            return {"resident": r.model_dump(mode="json"), "age": age(r.birth_date, state.current_date),
                    "gp": gp.model_dump(mode="json") if gp else None,
                    "allergies": [a.substance for a in care.allergies if a.resident_id == rid],
                    "representative": next((c.name for c in care.contacts if c.resident_id == rid
                                            and c.representation != "none"), None),
                    "risks": sorted(x.kind for x in care.risks if x.resident_id == rid and x.present)}
        if args.section == "contacts":
            return {"contacts": mine(care.contacts), "consents": mine(care.consents)}
        rows: list[dict[str, Any]]
        if args.section == "vitals":
            rows = [o for o in mine(care.observations) if not args.kind or o["kind"] == args.kind]
            rows.sort(key=lambda o: (o["measured_at"], o["obs_id"]), reverse=True)
        elif args.section in ("fluids", "excretion"):
            rows = sorted(mine(care.fluids if args.section == "fluids" else care.excretions),
                          key=lambda x: (x["at"], x["entry_id"]), reverse=True)
        elif args.section == "assessments":
            rows = sorted(mine(care.assessments), key=lambda x: (x["performed_at"], x["assessment_id"]), reverse=True)
        elif args.section == "report":
            rows = sorted(mine(care.reports), key=lambda x: (x["at"], x["entry_id"]), reverse=True)
        elif args.section == "orders":
            rows = sorted(mine(care.orders), key=lambda x: (x["entered_at"], x["order_id"]), reverse=True)
        elif args.section == "wounds":
            rows = [{**w, "assessments": [a.model_dump(mode="json") for a in care.wound_assessments
                                          if a.wound_id == w["wound_id"]]} for w in mine(care.wounds)]
        elif args.section == "documents":
            rows = sorted(mine(care.documents), key=lambda x: (x["filed_at"], x["document_id"]), reverse=True)
        elif args.section == "transfer":
            rows = sorted(mine(care.transfer_sheets), key=lambda x: (x["saved_at"], x["sheet_id"]), reverse=True)
        elif args.section == "appointments":
            rows = sorted(mine(care.appointments), key=lambda x: (x["date"], x["start"], x["appointment_id"]))
        else:
            source = {"diagnoses": care.diagnoses, "allergies": care.allergies, "risks": care.risks,
                      "careplan": care.careplan, "measures": care.measures, "medication": care.medication}[args.section]
            rows = mine(source)
        return _page(rows, args.limit, args.offset)


class OrdersArgs(ToolArgs):
    ward_id: str | None = None
    state: Literal["to_acknowledge", "active", "all"] = "to_acknowledge"


class OrdersTool(AppTool):
    app = "care"
    name = "care.orders"
    description = "List doctors' orders across residents: to acknowledge, active today, or all."
    args_model = OrdersArgs

    def execute(self, state: WorldState, args: OrdersArgs) -> list[dict[str, Any]]:
        care, today = _ctx().care, state.current_date
        wards = {r.resident_id: r.ward_id for r in care.residents}
        out = []
        for o in sorted(care.orders, key=lambda x: (x.entered_at, x.order_id), reverse=True):
            if args.ward_id and wards.get(o.resident_id) != args.ward_id:
                continue
            active = o.valid_from <= today and (o.valid_to is None or today <= o.valid_to)
            if args.state == "to_acknowledge" and o.acknowledged_by is not None or args.state == "active" and not active:
                continue
            out.append(o.model_dump(mode="json"))
        return out


class NoArgs(ToolArgs):
    pass


class HouseStandardTool(AppTool):
    app = "care"
    name = "care.house_standard"
    description = ("Read this home's care standards: assessments on admission and how often to repeat them, "
                   "repositioning intervals, vital-sign ranges for informing the GP, weighing, fluid target, follow-up "
                   "time, and the catalogue of measures with who may do them.")
    args_model = NoArgs

    def execute(self, state: WorldState, args: NoArgs) -> dict[str, Any]:
        data = _ctx().care.standard.model_dump(mode="json")
        data.pop("status", None)  # which values are still to be confirmed is not part of the home's document
        return data


class ScalesArgs(ToolArgs):
    scale_id: str | None = None


class ScalesTool(AppTool):
    app = "care"
    name = "care.scales"
    description = "List the assessment scales of this home, or one scale with its items, options and points."
    args_model = ScalesArgs

    def execute(self, state: WorldState, args: ScalesArgs) -> Any:
        loc = _ctx().app.locale
        if args.scale_id:
            try:
                s = scales.get_scale(args.scale_id)
            except scales.ScaleError as exc:
                raise ToolError(str(exc), "NOT_FOUND") from exc
            return {"scale_id": s.scale_id, "version": s.version, "title": s.title[loc], "risk_kind": s.risk_kind,
                    "items": [{"key": i.key, "label": i.label[loc],
                               "options": [{"key": o.key, "label": o.label[loc], "points": o.points} for o in i.options]}
                              for i in s.items],
                    "bands": [b.model_dump(mode="json") for b in s.bands]}
        return [{"scale_id": s.scale_id, "title": s.title[loc], "risk_kind": s.risk_kind} for s in scales.SCALES.values()]


class PlanViewArgs(ToolArgs):
    date: Day | None = None
    shift: Literal["early", "late", "night"] | None = None
    ward_id: str | None = None
    employee_id: str | None = None


class PlanViewTool(AppTool):
    app = "plan"
    name = "plan.view"
    description = ("View the daily plan of one shift (default: the current one): every item with resident, due time, "
                   "measure, duration, required qualification and skills, assigned person and status; the staff on "
                   "shift from the rota; and warnings (not assigned, not on shift, not qualified, same time, over shift "
                   "length).")
    args_model = PlanViewArgs

    def execute(self, state: WorldState, args: PlanViewArgs) -> dict[str, Any]:
        care = _ctx().care
        day, shift = current_shift(state)
        day, shift = args.date or day, args.shift or shift.value
        items = dailyplan.items_for(care, day, shift, args.ward_id)
        if args.employee_id:
            items = [i for i in items if i.assigned_to == args.employee_id]
        staff = dailyplan.on_shift(state, care, day, shift, args.ward_id)
        warn = dailyplan.warnings(state, care, day, shift, args.ward_id)
        return {"date": day.isoformat(), "shift": shift, "ward_id": args.ward_id, "now": _now(state),
                "staff": [{"employee_id": s.employee_id, "name": s.name, "qualifications": list(s.qualifications),
                           "skills": list(s.skills)} for s in staff],
                "items": [{**i.as_dict(), "warnings": warn.get(i.item_key, [])} for i in items]}


# ---------------------------------------------------------------- documentation

UNITS = {"bp": ("mmHg",), "pulse": ("/min",), "temperature": ("°C",), "spo2": ("%",), "weight": ("kg",),
         "height": ("cm",), "respiration": ("/min",), "pain": ("0-10",)}
PLAUSIBLE = {"bp": (50, 260), "pulse": (25, 220), "temperature": (33.0, 42.5), "spo2": (50, 100), "weight": (25, 250),
             "height": (120, 210), "respiration": (4, 60), "pain": (0, 10)}


class ObservationArgs(ToolArgs):
    resident_id: str
    kind: Literal["bp", "pulse", "temperature", "spo2", "glucose", "weight", "height", "respiration", "pain"]
    value: float = Field(description="The value; for blood pressure the systolic value.")
    value2: float | None = Field(default=None, description="Blood pressure only: the diastolic value.")
    unit: str
    measured_at: str = Field(description="YYYY-MM-DD HH:MM")
    measured_by: str | None = Field(default=None, description="Who measured (default: you).")
    method: str = ""
    source_ref: str | None = Field(default=None, description="Where the value comes from (e.g. a chat message id).")
    note: str = Field(default="", max_length=500)


class ObservationTool(AppTool):
    app = "care"
    name = "care.observation_record"
    description = ("Record a measured value for a resident (blood pressure, pulse, temperature, SpO2, blood glucose, "
                   "weight, height, respiration, pain level) with the time it was measured. The unit must be the "
                   "home's unit; implausible values are refused. BMI is computed, never entered.")
    args_model = ObservationArgs

    def execute(self, state: WorldState, args: ObservationArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id, present=True)
        allowed = UNITS.get(args.kind) or (("mg/dl",) if ctx.care.edition == "de" else ("mmol/L",))
        if args.unit not in allowed:
            raise ToolError(f"Unit for {args.kind} in this home: {allowed[0]}.")
        low, high = PLAUSIBLE.get(args.kind) or ((10, 800) if ctx.care.edition == "de" else (0.5, 45.0))
        if not low <= args.value <= high:
            raise ToolError("This value is outside what can be measured; please check it.")
        if args.kind == "bp":
            if args.value2 is None or not 20 <= args.value2 < args.value:
                raise ToolError("Blood pressure needs a diastolic value below the systolic value.")
        elif args.value2 is not None:
            raise ToolError("Only blood pressure has a second value.")
        at = _past(state, args.measured_at)
        obs = Observation(obs_id=_next_id("obs", [o.obs_id for o in ctx.care.observations]), resident_id=args.resident_id,
                          kind=args.kind, value=args.value, value2=args.value2, unit=args.unit, measured_at=at,
                          measured_by=args.measured_by or _user(), method=args.method, source_ref=args.source_ref,
                          note=args.note, entered_at=_now(state), entered_by=_user())
        ctx.care.observations.append(obs)
        rng = ctx.care.standard.vital_ranges.get(args.kind)
        return {"observation": obs.model_dump(mode="json"),
                "outside_house_range": bool(rng and rng.outside(args.value))}


class FluidArgs(ToolArgs):
    resident_id: str
    direction: Literal["in", "out"]
    kind: Literal["drink", "tube_feed", "infusion", "urine", "vomit", "drain"]
    ml: int = Field(ge=1, le=3000)
    at: str
    source_ref: str | None = None


class FluidTool(AppTool):
    app = "care"
    name = "care.fluid_record"
    description = "Record fluid intake or output in ml for a resident, with the time."
    args_model = FluidArgs

    def execute(self, state: WorldState, args: FluidArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id, present=True)
        if (args.direction == "in") != (args.kind in ("drink", "tube_feed", "infusion")):
            raise ToolError("This kind does not fit the direction.")
        entry = FluidEntry(entry_id=_next_id("fluid", [f.entry_id for f in ctx.care.fluids]), resident_id=args.resident_id,
                           direction=args.direction, kind=args.kind, ml=args.ml, at=_past(state, args.at),
                           source_ref=args.source_ref, entered_by=_user())
        ctx.care.fluids.append(entry)
        return {"entry": entry.model_dump(mode="json")}


class ExcretionArgs(ToolArgs):
    resident_id: str
    kind: Literal["urine", "stool"]
    continence: Literal["continent", "incontinent", "catheter"]
    at: str
    note: str = Field(default="", max_length=300)
    source_ref: str | None = None


class ExcretionTool(AppTool):
    app = "care"
    name = "care.excretion_record"
    description = "Record urine or stool for a resident (continent, incontinent, catheter), with the time."
    args_model = ExcretionArgs

    def execute(self, state: WorldState, args: ExcretionArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id, present=True)
        entry = ExcretionEntry(entry_id=_next_id("excr", [x.entry_id for x in ctx.care.excretions]),
                               resident_id=args.resident_id, kind=args.kind, continence=args.continence,
                               note=args.note, at=_past(state, args.at), source_ref=args.source_ref, entered_by=_user())
        ctx.care.excretions.append(entry)
        return {"entry": entry.model_dump(mode="json")}


class AssessmentArgs(ToolArgs):
    resident_id: str
    scale_id: str
    answers: dict[str, str] = Field(description="Item key → option key, one for every item of the scale.")
    performed_at: str
    source_refs: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=500)


class AssessmentTool(AppTool):
    app = "care"
    name = "care.assessment_submit"
    description = ("Submit an assessment: one option per item of the scale. The app computes the total, the risk "
                   "class (house bands) and the next due date (house standard), and updates the resident's risk.")
    args_model = AssessmentArgs

    def execute(self, state: WorldState, args: AssessmentArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id, present=True)
        try:
            scale = scales.get_scale(args.scale_id)
            total, risk = scales.score(scale, args.answers)
        except scales.ScaleError as exc:
            raise ToolError(str(exc)) from exc
        at = _past(state, args.performed_at)
        every = ctx.care.standard.reassess_every_days.get(scale.scale_id)
        day = _when(at).date()
        record = AssessmentRecord(
            assessment_id=_next_id("asmt", [a.assessment_id for a in ctx.care.assessments]), resident_id=args.resident_id,
            scale_id=scale.scale_id, scale_version=scale.version, answers=dict(sorted(args.answers.items())),
            total=total, risk_class=risk, performed_at=at, performed_by=_user(),
            next_due=day + timedelta(days=every) if every else None, source_refs=args.source_refs, note=args.note)
        ctx.care.assessments.append(record)
        if scale.risk_kind != "dependency":
            _set_risk(ctx.care, args.resident_id, scale.risk_kind, risk != "none",
                      f"{scale.title[ctx.app.locale]}: {total}", day, record.assessment_id, record.next_due)
        return {"assessment": record.model_dump(mode="json")}


def _set_risk(care: CareState, rid: str, kind: str, present: bool, reason: str, day: date,
              assessment_id: str | None, review_on: date | None) -> RiskEntry:
    current = next((x for x in care.risks if x.resident_id == rid and x.kind == kind), None)
    if current is None:
        current = RiskEntry(risk_id=f"{rid}-risk-{kind}", resident_id=rid, kind=kind, present=present, reason=reason,
                            assessment_id=assessment_id, since=day, review_on=review_on)
        care.risks.append(current)
        return current
    if current.present != present:
        current.since = day
    current.present, current.reason = present, reason
    current.assessment_id, current.review_on = assessment_id, review_on
    return current


class RiskArgs(ToolArgs):
    resident_id: str
    kind: Literal["pressure_ulcer", "falls", "dehydration", "malnutrition", "contractures", "pain", "incontinence"]
    present: bool
    reason: str = Field(min_length=1, max_length=500)
    review_on: date | None = None


class RiskTool(AppTool):
    app = "care"
    name = "care.risk_set"
    description = "Set a prevention need (risk) as present or not present, with the reason and a review date."
    args_model = RiskArgs

    def execute(self, state: WorldState, args: RiskArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id)
        if not _meaningful(args.reason):
            raise ToolError(REASON)
        risk = _set_risk(ctx.care, args.resident_id, args.kind, args.present, args.reason, state.current_date, None,
                         args.review_on)
        return {"risk": risk.model_dump(mode="json")}


# ---------------------------------------------------------------- care plan and measures

class PlanEntryArgs(ToolArgs):
    resident_id: str
    theme: str = ""
    finding: str = Field(default="", max_length=2000)
    problem: str = Field(default="", max_length=2000)
    resource: str = Field(default="", max_length=2000)
    goal: str = Field(default="", max_length=2000)
    risk_kinds: list[str] = Field(default_factory=list)
    review_on: date


class PlanEntryTool(AppTool):
    app = "care"
    name = "care.careplan_entry_add"
    description = ("Add a care-plan entry in this home's model: structure model homes give a theme and a finding; "
                   "problem/goal homes give problem, resource and goal. Measures are added separately.")
    args_model = PlanEntryArgs

    def execute(self, state: WorldState, args: PlanEntryArgs) -> dict[str, Any]:
        ctx = _ctx()
        _resident(args.resident_id)
        model = ctx.care.careplan_model
        errors = careplan.entry_errors(model, args.model_dump())
        if errors:
            raise ToolError("Care plan: " + "; ".join(errors) + ".")
        if args.review_on <= state.current_date:
            raise ToolError("The review date must be in the future.")
        entry = CarePlanEntry(entry_id=_next_id("cpe", [e.entry_id for e in ctx.care.careplan]),
                              resident_id=args.resident_id, model=model, theme=args.theme, finding=args.finding,
                              problem=args.problem, resource=args.resource, goal=args.goal,
                              risk_kinds=sorted(set(args.risk_kinds)), created_at=_now(state), created_by=_user(),
                              review_on=args.review_on)
        ctx.care.careplan.append(entry)
        return {"entry": entry.model_dump(mode="json")}


class PlanEntryUpdateArgs(ToolArgs):
    entry_id: str
    review_on: date | None = None
    status: Literal["active", "ended"] | None = None


class PlanEntryUpdateTool(AppTool):
    app = "care"
    name = "care.careplan_entry_update"
    description = "Change the review date of a care-plan entry, or end it."
    args_model = PlanEntryUpdateArgs

    def execute(self, state: WorldState, args: PlanEntryUpdateArgs) -> dict[str, Any]:
        entry = next((e for e in _ctx().care.careplan if e.entry_id == args.entry_id), None)
        if entry is None:
            raise ToolError("Care-plan entry not found.", "NOT_FOUND")
        if args.review_on is None and args.status is None:
            raise ToolError("Nothing to change.")
        if args.review_on is not None:
            if args.review_on <= state.current_date:
                raise ToolError("The review date must be in the future.")
            entry.review_on = args.review_on
        if args.status is not None:
            entry.status = args.status
        return {"entry": entry.model_dump(mode="json")}


RANK = {Qualification.CA: 0, Qualification.TL: 1, Qualification.RN: 2}


class ScheduleArgs(ToolArgs):
    kind: Literal["times", "interval", "weekly"]
    times: list[str] = Field(default_factory=list, description="HH:MM (times, weekly)")
    every_hours: int | None = Field(default=None, ge=1, le=24)
    night_every_hours: int | None = Field(default=None, ge=1, le=24)
    anchor: str = Field(default="06:00", pattern=HHMM)
    weekdays: list[int] = Field(default_factory=list, description="0 = Monday (weekly)")


def _schedule(args: ScheduleArgs) -> Schedule:
    if any(not re.match(HHMM, t) for t in args.times):
        raise ToolError("Times must be HH:MM.")
    if args.kind in ("times", "weekly") and not args.times:
        raise ToolError("Please give at least one time.")
    if args.kind == "weekly" and (not args.weekdays or any(not 0 <= d <= 6 for d in args.weekdays)):
        raise ToolError("Please give weekdays from 0 (Monday) to 6 (Sunday).")
    if args.kind == "interval" and not args.every_hours:
        raise ToolError("Please give the interval in hours.")
    return Schedule(kind=args.kind, times=sorted(set(args.times)), every_hours=args.every_hours,
                    night_every_hours=args.night_every_hours, anchor=args.anchor, weekdays=sorted(set(args.weekdays)))


class MeasureArgs(ToolArgs):
    resident_id: str
    catalogue_key: str
    schedule: ScheduleArgs
    start: date
    end: date | None = None
    entry_id: str | None = None
    order_id: str | None = None
    qualification: Qualification | None = Field(default=None, description="Default: the catalogue's; never lower.")
    note: str = Field(default="", max_length=500)


class MeasureTool(AppTool):
    app = "care"
    name = "care.measure_add"
    description = ("Add a measure from the home's catalogue to a resident's care plan with its schedule. It appears "
                   "in the daily plans from its start date. Measures from a doctor's order name the order.")
    args_model = MeasureArgs

    def execute(self, state: WorldState, args: MeasureArgs) -> dict[str, Any]:
        ctx = _ctx()
        care = ctx.care
        _resident(args.resident_id)
        entry = care.standard.entry(args.catalogue_key)
        if entry is None:
            raise ToolError("Not a measure of this home's catalogue.", "NOT_FOUND")
        order = None
        if args.order_id:
            order = next((o for o in care.orders if o.order_id == args.order_id and o.resident_id == args.resident_id), None)
            if order is None:
                raise ToolError("Order not found for this resident.", "NOT_FOUND")
        if args.catalogue_key == "insulin" and (order is None or order.kind != "medication"):
            raise ToolError("Medicine is given only as the doctor ordered it; name the medication order.")
        if args.entry_id and not any(e.entry_id == args.entry_id and e.resident_id == args.resident_id
                                     for e in care.careplan):
            raise ToolError("Care-plan entry not found for this resident.", "NOT_FOUND")
        qual = args.qualification or entry.qualification
        if RANK[qual] < RANK[entry.qualification]:
            raise ToolError("This measure needs at least the qualification the house standard sets.")
        if args.start < state.current_date or args.end is not None and args.end < args.start:
            raise ToolError("Start today or later; the end must not be before the start.")
        m = Measure(measure_id=_next_id("msr", [x.measure_id for x in care.measures]), resident_id=args.resident_id,
                    entry_id=args.entry_id, catalogue_key=entry.key, label=entry.label[ctx.app.locale],
                    schedule=_schedule(args.schedule), duration_min=entry.duration_min, qualification=qual,
                    skills=list(entry.skills), start=args.start, end=args.end,
                    source="order" if order else "care_plan", order_id=args.order_id, note=args.note,
                    created_by=_user())
        care.measures.append(m)
        return {"measure": m.model_dump(mode="json")}


class MeasureEndArgs(ToolArgs):
    measure_id: str
    end: date
    reason: str = Field(min_length=1, max_length=300)


class MeasureEndTool(AppTool):
    app = "care"
    name = "care.measure_end"
    description = "End a measure on a date (it disappears from daily plans after that date), with the reason."
    args_model = MeasureEndArgs

    def execute(self, state: WorldState, args: MeasureEndArgs) -> dict[str, Any]:
        care = _ctx().care
        m = next((x for x in care.measures if x.measure_id == args.measure_id), None)
        if m is None:
            raise ToolError("Measure not found.", "NOT_FOUND")
        if not _meaningful(args.reason):
            raise ToolError(REASON)
        if m.order_id and m.catalogue_key == "insulin":
            raise ToolError("Medicine follows the doctor's order; it ends only when the order ends.")
        if args.end < max(m.start, state.current_date - timedelta(days=1)):
            raise ToolError("The end cannot be before the start or in the past.")
        m.end = args.end
        m.note = (m.note + " " if m.note else "") + f"[{_now(state)}] {args.reason}"
        return {"measure": m.model_dump(mode="json")}


class ReportArgs(ToolArgs):
    resident_id: str
    category: Literal["general", "observation", "incident", "communication", "handover"] = "general"
    text: str = Field(min_length=1, max_length=4000)
    observation_ids: list[str] = Field(default_factory=list)
    informed: list[str] = Field(default_factory=list, description="Who was informed (names or roles).")
    correction_of: str | None = None


class ReportTool(AppTool):
    app = "care"
    name = "care.report_write"
    description = ("Write a care-report entry for a resident. Entries cannot be changed; to correct one, write a new "
                   "entry with correction_of.")
    args_model = ReportArgs

    def execute(self, state: WorldState, args: ReportArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        known = {o.obs_id for o in care.observations if o.resident_id == args.resident_id}
        if any(o not in known for o in args.observation_ids):
            raise ToolError("Linked value not found for this resident.", "NOT_FOUND")
        if args.correction_of and not any(r.entry_id == args.correction_of and r.resident_id == args.resident_id
                                          for r in care.reports):
            raise ToolError("Entry to correct not found for this resident.", "NOT_FOUND")
        entry = CareReportEntry(entry_id=_next_id("rep", [r.entry_id for r in care.reports]), resident_id=args.resident_id,
                                at=_now(state), author=_user(), category=args.category, text=args.text.strip(),
                                observation_ids=args.observation_ids, informed=args.informed,
                                correction_of=args.correction_of)
        care.reports.append(entry)
        return {"entry": entry.model_dump(mode="json")}


class AcknowledgeArgs(ToolArgs):
    order_id: str


class AcknowledgeTool(AppTool):
    app = "care"
    name = "care.order_acknowledge"
    description = ("Acknowledge a doctor's order (you have read it). Orders are entered by the doctor and cannot be "
                   "changed here; transfer what they ask into measures yourself.")
    args_model = AcknowledgeArgs

    def execute(self, state: WorldState, args: AcknowledgeArgs) -> dict[str, Any]:
        order = next((o for o in _ctx().care.orders if o.order_id == args.order_id), None)
        if order is None:
            raise ToolError("Order not found.", "NOT_FOUND")
        if order.acknowledged_by is not None:
            raise ToolError("This order has already been acknowledged.")
        order.acknowledged_by, order.acknowledged_at = _user(), _now(state)
        return {"order": order.model_dump(mode="json")}


class AppointmentArgs(ToolArgs):
    resident_id: str
    kind: Literal["gp_visit", "specialist", "therapy", "hospital", "relatives", "other"]
    date: Day
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)
    place: str = Field(min_length=1, max_length=200)
    with_whom: str = Field(default="", max_length=200)
    transport_needed: bool = False
    escort_qualification: Qualification | None = None


class AppointmentTool(AppTool):
    app = "care"
    name = "care.appointment_create"
    description = ("Create an appointment for a resident (visit, therapy, specialist, hospital, relatives). With an "
                   "escort qualification it adds an escort item to that shift's daily plan.")
    args_model = AppointmentArgs

    def execute(self, state: WorldState, args: AppointmentArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        if args.date < state.current_date or args.end <= args.start:
            raise ToolError("Appointments start today or later and end after they start.")
        a = Appointment(appointment_id=_next_id("apt", [x.appointment_id for x in care.appointments]),
                        resident_id=args.resident_id, kind=args.kind, date=args.date, start=args.start, end=args.end,
                        place=args.place, with_whom=args.with_whom, transport_needed=args.transport_needed,
                        escort_qualification=args.escort_qualification, created_by=_user())
        care.appointments.append(a)
        return {"appointment": a.model_dump(mode="json")}


class AppointmentCancelArgs(ToolArgs):
    appointment_id: str
    reason: str = Field(min_length=1, max_length=300)


class AppointmentCancelTool(AppTool):
    app = "care"
    name = "care.appointment_cancel"
    description = "Cancel a planned appointment, with the reason."
    args_model = AppointmentCancelArgs

    def execute(self, state: WorldState, args: AppointmentCancelArgs) -> dict[str, Any]:
        a = next((x for x in _ctx().care.appointments if x.appointment_id == args.appointment_id), None)
        if a is None:
            raise ToolError("Appointment not found.", "NOT_FOUND")
        if a.status != "planned":
            raise ToolError("Only planned appointments can be cancelled.")
        if not _meaningful(args.reason):
            raise ToolError(REASON)
        a.status = "cancelled"
        return {"appointment": a.model_dump(mode="json")}


# ---------------------------------------------------------------- daily plan

def _item(state: WorldState, item_key: str) -> dailyplan.PlanItem:
    try:
        due = datetime.strptime(item_key.rsplit("@", 1)[1], "%Y-%m-%dT%H:%M")
    except (IndexError, ValueError) as exc:
        raise ToolError("Plan item not found.", "NOT_FOUND") from exc
    day, shift = dailyplan.shift_at(due)
    item = next((i for i in dailyplan.items_for(_ctx().care, day, shift) if i.item_key == item_key), None)
    if item is None:
        raise ToolError("Plan item not found.", "NOT_FOUND")
    return item


def _open_shift(state: WorldState, item: dailyplan.PlanItem) -> None:
    """Only today's and tomorrow's plans can be changed; earlier shifts are history."""
    if not state.current_date - timedelta(days=1) <= item.date <= state.current_date + timedelta(days=1):
        raise ToolError("Only today's and tomorrow's plans can be changed.")
    _, end = dailyplan.shift_bounds(item.date, item.shift)
    if end <= _when(_now(state)):
        raise ToolError("This shift is over.")


class AssignArgs(ToolArgs):
    item_key: str
    employee_id: str | None = Field(description="Staff member on that shift; null removes the assignment.")


class AssignTool(AppTool):
    app = "plan"
    name = "plan.assign"
    description = ("Assign a daily-plan item to a staff member (or remove the assignment). The app warns when the "
                   "person is not on that shift or lacks the qualification or skill; it does not block.")
    args_model = AssignArgs

    def execute(self, state: WorldState, args: AssignArgs) -> dict[str, Any]:
        item = _item(state, args.item_key)
        _open_shift(state, item)
        if item.status in ("done", "not_done"):
            raise ToolError("This item is already closed.")
        warnings: list[str] = []
        if args.employee_id is not None:
            if not any(e.employee_id == args.employee_id for e in state.employees):
                raise ToolError("Employee not found.", "NOT_FOUND")
            staff = {s.employee_id: s for s in dailyplan.on_shift(state, _ctx().care, item.date, item.shift)}
            warnings = dailyplan.eligibility(item, staff.get(args.employee_id))
        s = dailyplan.set_state(_ctx().care, item.item_key, at=_now(state), assigned_to=args.employee_id)
        return {"item_key": item.item_key, "assigned_to": s.assigned_to, "warnings": warnings}


class StatusArgs(ToolArgs):
    item_key: str
    status: Literal["open", "done", "not_done", "handed_over"]
    reason: str = Field(default="", max_length=300)


class StatusTool(AppTool):
    app = "plan"
    name = "plan.set_status"
    description = ("Mark a daily-plan item done, not done (reason needed) or handed over to the next shift (reason "
                   "needed), or open again.")
    args_model = StatusArgs

    def execute(self, state: WorldState, args: StatusArgs) -> dict[str, Any]:
        item = _item(state, args.item_key)
        _open_shift(state, item)
        if args.status in ("not_done", "handed_over") and not _meaningful(args.reason):
            raise ToolError(REASON)
        if args.status == "done" and item.due > _when(_now(state)) + timedelta(minutes=_ctx().care.standard.plan_window_minutes):
            raise ToolError("This item is not due yet.")
        s = dailyplan.set_state(_ctx().care, item.item_key, at=_now(state), status=args.status,
                                reason=args.reason.strip(), by="")
        return {"item_key": item.item_key, "status": s.status, "reason": s.reason}


class AddItemArgs(ToolArgs):
    resident_id: str
    date: Day
    due: str = Field(pattern=HHMM)
    catalogue_key: str
    reason: str = Field(min_length=1, max_length=300)
    qualification: Qualification | None = None


class AddItemTool(AppTool):
    app = "plan"
    name = "plan.add_item"
    description = ("Add a one-off item to a daily plan (e.g. a follow-up measurement at a given time), today or "
                   "tomorrow, with the reason.")
    args_model = AddItemArgs

    def execute(self, state: WorldState, args: AddItemArgs) -> dict[str, Any]:
        ctx = _ctx()
        care = ctx.care
        _resident(args.resident_id, present=True)
        entry = care.standard.entry(args.catalogue_key)
        if entry is None:
            raise ToolError("Not a measure of this home's catalogue.", "NOT_FOUND")
        if args.catalogue_key == "insulin":
            raise ToolError("Medicine is given only as the doctor ordered it.")
        if not _meaningful(args.reason):
            raise ToolError(REASON)
        due = datetime.combine(args.date, datetime.min.time()).replace(hour=int(args.due[:2]), minute=int(args.due[3:]))
        if not _when(_now(state)) <= due <= _when(_now(state)) + timedelta(days=2):
            raise ToolError("One-off items are for the next 48 hours.")
        qual = args.qualification or entry.qualification
        if RANK[qual] < RANK[entry.qualification]:
            raise ToolError("This measure needs at least the qualification the house standard sets.")
        x = ExtraPlanItem(item_id=_next_id("xitem", [i.item_id for i in care.plan_extra]), resident_id=args.resident_id,
                          date=args.date, due=args.due, label=entry.label[ctx.app.locale], catalogue_key=entry.key,
                          duration_min=entry.duration_min, qualification=qual, skills=list(entry.skills),
                          reason=args.reason.strip(), created_at=_now(state), created_by=_user())
        care.plan_extra.append(x)
        return {"item": x.model_dump(mode="json"), "item_key": f"{x.item_id}@{due:%Y-%m-%dT%H:%M}"}



# ---------------------------------------------------------------- admission, contacts, allergies, status

class AdmitArgs(ToolArgs):
    given_name: str = Field(min_length=1, max_length=60)
    family_name: str = Field(min_length=1, max_length=60)
    sex: Literal["f", "m"]
    birth_date: date
    birth_place: str = Field(min_length=1, max_length=80)
    ward_id: str
    room: str = Field(min_length=1, max_length=10)
    gp_id: str
    care_level: int | None = Field(default=None, ge=1, le=5, description="German homes: care level 1–5.")
    funding_note: str = Field(default="", max_length=80, description="UK homes: how the place is funded.")
    religion: str = Field(default="", max_length=60)
    advance_directive: bool = False


class AdmitTool(AppTool):
    app = "care"
    name = "care.admit"
    description = ("Admit a new resident today: master data, unit and a free room, GP. Contacts, allergies, values, "
                   "assessments and the care plan are added with their own functions afterwards.")
    args_model = AdmitArgs

    def execute(self, state: WorldState, args: AdmitArgs) -> dict[str, Any]:
        ctx = _ctx()
        care = ctx.care
        if args.ward_id not in ctx.app.unit_names:
            raise ToolError("Unit not found.", "NOT_FOUND")
        gp = care.physician(args.gp_id)
        if gp is None or gp.kind != "gp":
            raise ToolError("GP not found.", "NOT_FOUND")
        if any(r.room == args.room and r.status in ("present", "in_hospital", "on_leave") for r in care.residents):
            raise ToolError("This room is taken.")
        if args.birth_date > state.current_date - timedelta(days=365 * 18):
            raise ToolError("Please check the date of birth.")
        if care.edition == "de" and args.care_level is None:
            raise ToolError("Please give the care level.")
        rid = _next_id("res", [r.resident_id for r in care.residents])
        r = Resident(resident_id=rid, given_name=args.given_name.strip(), family_name=args.family_name.strip(), sex=args.sex,
                     birth_date=args.birth_date, birth_place=args.birth_place.strip(), ward_id=args.ward_id,
                     room=args.room.strip().upper(), admitted_on=state.current_date, care_level=args.care_level,
                     funding_note=args.funding_note.strip(), gp_id=args.gp_id, religion=args.religion.strip(),
                     advance_directive=args.advance_directive)
        care.residents.append(r)
        return {"resident": r.model_dump(mode="json")}


class ContactArgs(ToolArgs):
    resident_id: str
    name: str = Field(min_length=1, max_length=80)
    relation: str = Field(min_length=1, max_length=40)
    phone: str = Field(min_length=3, max_length=40)
    mail: str = Field(default="", max_length=120)
    emergency: bool = False
    representation: Literal["none", "guardian", "power_of_attorney", "lpa_health"] = "none"
    consent_document_id: str | None = Field(default=None, description="Signed release that lets this contact receive "
                                                                       "information (a document in the record).")


class ContactTool(AppTool):
    app = "care"
    name = "care.contact_add"
    description = ("Add a relative or other contact to a resident's record. The contact may receive information only "
                   "with a signed release in the record (consent_document_id) or as legal representative.")
    args_model = ContactArgs

    def execute(self, state: WorldState, args: ContactArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        if args.consent_document_id and not any(d.document_id == args.consent_document_id and
                                                d.resident_id == args.resident_id for d in care.documents):
            raise ToolError("Document not found in this resident's record.", "NOT_FOUND")
        mail = args.mail.strip()
        if mail and any(c.mail.casefold() == mail.casefold() for c in care.contacts) or \
                mail and any(c.address.casefold() == mail.casefold() for c in _ctx().app.contacts):
            raise ToolError("This mail address belongs to another contact.")
        cid = _next_id("contact", [c.contact_id for c in care.contacts])
        allowed = args.representation != "none" or args.consent_document_id is not None
        c = ResidentContact(contact_id=cid, resident_id=args.resident_id, name=args.name.strip(), relation=args.relation.strip(),
                            phone=args.phone.strip(), mail=args.mail.strip(), emergency=args.emergency,
                            representation=args.representation,
                            representation_scope=["health", "residence"] if args.representation != "none" else [],
                            information_allowed=allowed)
        care.contacts.append(c)
        if args.consent_document_id:
            care.consents.append(Consent(consent_id=_next_id("consent", [x.consent_id for x in care.consents]),
                                         resident_id=args.resident_id, kind="confidentiality_release", contact_id=cid,
                                         granted_by=_resident(args.resident_id).name, granted_on=state.current_date))
        return {"contact": c.model_dump(mode="json")}


class AllergyArgs(ToolArgs):
    resident_id: str
    substance: str = Field(min_length=1, max_length=80)
    reaction: str = Field(min_length=1, max_length=120)
    severity: Literal["mild", "moderate", "severe"]
    source_document_id: str = Field(description="The letter in the record that names the allergy.")


class AllergyTool(AppTool):
    app = "care"
    name = "care.allergy_add"
    description = "Record an allergy or intolerance as a doctor's letter in the record states it (name the document)."
    args_model = AllergyArgs

    def execute(self, state: WorldState, args: AllergyArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        doc = next((d for d in care.documents if d.document_id == args.source_document_id
                    and d.resident_id == args.resident_id), None)
        if doc is None:
            raise ToolError("File the letter in the record first and name it as the source.", "NOT_FOUND")
        if any(a.resident_id == args.resident_id and a.substance.casefold() == args.substance.strip().casefold()
               for a in care.allergies):
            raise ToolError("This allergy is already recorded.")
        a = Allergy(allergy_id=_next_id("allergy", [x.allergy_id for x in care.allergies]), resident_id=args.resident_id,
                    substance=args.substance.strip(), reaction=args.reaction.strip(), severity=args.severity,
                    source=doc.title, entered_by=_user(), entered_on=state.current_date)
        care.allergies.append(a)
        return {"allergy": a.model_dump(mode="json")}


class StatusArgs2(ToolArgs):
    resident_id: str
    status: Literal["present", "in_hospital", "on_leave"]
    reason: str = Field(min_length=1, max_length=300)


class ResidentStatusTool(AppTool):
    app = "care"
    name = "care.status_set"
    description = ("Change where the resident is: in the home, in hospital, or on leave (with the reason). While away, "
                   "the resident's items leave the daily plan.")
    args_model = StatusArgs2

    def execute(self, state: WorldState, args: StatusArgs2) -> dict[str, Any]:
        r = _resident(args.resident_id)
        if r.status == "moved_out":
            raise ToolError("The resident has moved out.")
        if r.status == args.status:
            raise ToolError("The resident already has this status.")
        if not _meaningful(args.reason):
            raise ToolError(REASON)
        before, r.status = r.status, args.status
        return {"resident_id": r.resident_id, "before": before, "status": r.status}


# ---------------------------------------------------------------- wounds

class WoundArgs(ToolArgs):
    resident_id: str
    location: str = Field(min_length=2, max_length=80)
    kind: Literal["pressure_ulcer", "skin_tear", "leg_ulcer", "surgical", "other"]
    category: Literal["1", "2", "3", "4", "unstageable", ""] = ""
    first_seen: date
    origin: Literal["in_house", "brought_in"]


class WoundTool(AppTool):
    app = "care"
    name = "care.wound_create"
    description = "Start the documentation of a wound (location, kind, pressure-ulcer category, first seen, origin)."
    args_model = WoundArgs

    def execute(self, state: WorldState, args: WoundArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        if (args.kind == "pressure_ulcer") != bool(args.category):
            raise ToolError("Pressure ulcers need a category; other wounds have none.")
        if args.first_seen > state.current_date:
            raise ToolError("This date is in the future.")
        w = Wound(wound_id=_next_id("wound", [x.wound_id for x in care.wounds]), resident_id=args.resident_id,
                  location=args.location.strip(), kind=args.kind, category=args.category, first_seen=args.first_seen,
                  origin=args.origin, created_by=_user())
        care.wounds.append(w)
        return {"wound": w.model_dump(mode="json")}


class WoundAssessArgs(ToolArgs):
    wound_id: str
    at: str
    length_mm: int = Field(ge=0, le=500)
    width_mm: int = Field(ge=0, le=500)
    depth_mm: int = Field(ge=0, le=200)
    exudate: Literal["none", "low", "moderate", "high"]
    wound_bed: str = Field(default="", max_length=200)
    dressing: str = Field(default="", max_length=200)
    next_change: date | None = None
    photo_taken: bool = False
    healed: bool = False


class WoundAssessTool(AppTool):
    app = "care"
    name = "care.wound_assess"
    description = "Document a wound: size in mm, exudate, wound bed, dressing used, next dressing change; or mark it healed."
    args_model = WoundAssessArgs

    def execute(self, state: WorldState, args: WoundAssessArgs) -> dict[str, Any]:
        care = _ctx().care
        w = next((x for x in care.wounds if x.wound_id == args.wound_id), None)
        if w is None:
            raise ToolError("Wound not found.", "NOT_FOUND")
        if w.status == "healed":
            raise ToolError("This wound is closed as healed.")
        if args.next_change is not None and args.next_change < state.current_date:
            raise ToolError("The next change cannot be in the past.")
        a = WoundAssessment(wound_assessment_id=_next_id("woundas", [x.wound_assessment_id for x in care.wound_assessments]),
                            wound_id=w.wound_id, resident_id=w.resident_id, at=_past(state, args.at),
                            length_mm=args.length_mm, width_mm=args.width_mm, depth_mm=args.depth_mm, exudate=args.exudate,
                            wound_bed=args.wound_bed, dressing=args.dressing, next_change=args.next_change,
                            photo_taken=args.photo_taken, by=_user())
        care.wound_assessments.append(a)
        if args.healed:
            w.status = "healed"
        return {"assessment": a.model_dump(mode="json"), "wound_status": w.status}


# ---------------------------------------------------------------- documents and printing

class AttachArgs(ToolArgs):
    attachment_id: str
    resident_id: str
    kind: Literal["discharge_letter", "findings", "lab_report", "consent_form", "transport_form", "other"]
    title: str = Field(min_length=1, max_length=120)


class AttachTool(AppTool):
    app = "care"
    name = "care.document_file"
    description = "File a mail attachment (letter, findings, lab report, signed form) in a resident's record."
    args_model = AttachArgs

    def execute(self, state: WorldState, args: AttachArgs) -> dict[str, Any]:
        ctx = _ctx()
        care = ctx.care
        _resident(args.resident_id)
        found = next(((m, a) for m in ctx.app.mails for a in m.attachments if a.attachment_id == args.attachment_id), None)
        if found is None:
            raise ToolError("Attachment not found.", "NOT_FOUND")
        mail, att = found
        if any(d.source_attachment_id == att.attachment_id and d.resident_id == args.resident_id for d in care.documents):
            raise ToolError("This attachment is already filed for this resident.")
        d = CareDocument(document_id=_next_id("doc", [x.document_id for x in care.documents]), resident_id=args.resident_id,
                         kind=args.kind, title=args.title.strip(), dated=date.fromisoformat(mail.sent_at[:10]),
                         sender=mail.sender_name, text=att.content, source_attachment_id=att.attachment_id,
                         filed_at=_now(state), filed_by=_user())
        care.documents.append(d)
        return {"document": {k: v for k, v in d.model_dump(mode="json").items() if k != "text"}}


class TransferArgs(ToolArgs):
    resident_id: str
    destination: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=2, max_length=500, description="The reason as the doctor or emergency service gave it.")
    mobility: Literal["walks_alone", "walks_with_aid", "wheelchair", "bedbound"]
    continence: Literal["continent", "urine_incontinent", "double_incontinent", "catheter"]
    orientation: Literal["oriented", "partly_oriented", "disoriented"]
    aids_sent: list[Literal["glasses", "hearing_aid", "dentures", "walking_frame", "wheelchair"]] = Field(default_factory=list)
    isolation: bool = False
    contact_for_questions: str = Field(default="", max_length=120)


class TransferTool(AppTool):
    app = "care"
    name = "care.transfer_sheet_save"
    description = ("Save the hospital transfer sheet for a resident. Identity, allergies, diagnoses, medication and the "
                   "latest values are taken from the record when it is printed; you fill in the rest.")
    args_model = TransferArgs

    def execute(self, state: WorldState, args: TransferArgs) -> dict[str, Any]:
        care = _ctx().care
        _resident(args.resident_id)
        sheet = TransferSheet(sheet_id=_next_id("transfer", [x.sheet_id for x in care.transfer_sheets]),
                              resident_id=args.resident_id, destination=args.destination.strip(), reason=args.reason.strip(),
                              mobility=args.mobility, continence=args.continence, orientation=args.orientation,
                              aids_sent=sorted(set(args.aids_sent)), isolation=args.isolation,
                              contact_for_questions=args.contact_for_questions.strip(), saved_at=_now(state), saved_by=_user())
        care.transfer_sheets.append(sheet)
        return {"sheet": sheet.model_dump(mode="json")}


class PrintArgs(ToolArgs):
    kind: Literal["transfer_sheet", "medication_plan", "daily_plan", "care_report", "document", "round_sheet"]
    resident_id: str | None = None
    ref: str = Field(default="", description="transfer_sheet: sheet id (default: the latest); document: document id; "
                                             "daily_plan: YYYY-MM-DD/shift or YYYY-MM-DD/shift/employee id, or "
                                             "no ref with resident_id for that resident's own plan today; round_sheet: round id (default: "
                                             "today's latest round).")
    copies: int = Field(default=1, ge=1, le=5)
    printer: str


class PrintTool(AppTool):
    app = "print"
    name = "print.submit"
    description = ("Send a printout to one of the home's printers: transfer sheet, medication plan, daily plan, care report, "
                   "a filed document or the sheet for a doctor's round.")
    args_model = PrintArgs

    def execute(self, state: WorldState, args: PrintArgs) -> dict[str, Any]:
        care = _ctx().care
        if args.printer not in care.printers:
            raise ToolError("Printer not found. Printers: " + ", ".join(care.printers) + ".", "NOT_FOUND")
        ref = args.ref
        if args.kind in ("transfer_sheet", "medication_plan", "care_report", "document"):
            if not args.resident_id:
                raise ToolError("Please choose the resident.")
            _resident(args.resident_id)
        if args.kind == "transfer_sheet":
            sheets = [x for x in care.transfer_sheets if x.resident_id == args.resident_id]
            sheet = next((x for x in sheets if x.sheet_id == ref), None) if ref else (sheets[-1] if sheets else None)
            if sheet is None:
                raise ToolError("Save the transfer sheet first.", "NOT_FOUND")
            ref = sheet.sheet_id
        elif args.kind == "document":
            if not any(d.document_id == ref and d.resident_id == args.resident_id for d in care.documents):
                raise ToolError("Document not found in this resident's record.", "NOT_FOUND")
        elif args.kind == "round_sheet":  # the sheet the nurse takes on the doctor's round (0.6.0, head of nursing C9)
            today = date.fromisoformat(_now(state)[:10])
            plans = [x for x in care.rounds if (x.round_id == ref if ref else x.date == today)]
            if not plans:
                raise ToolError("Plan the round first." if not ref else "Round not found.", "NOT_FOUND")
            ref = plans[-1].round_id
        elif args.kind == "daily_plan" and args.resident_id and not ref:  # the resident's own plan for today
            _resident(args.resident_id)
            ref = f"{_now(state)[:10]}/resident"
        elif args.kind == "daily_plan":
            parts = ref.split("/")
            try:
                date.fromisoformat(parts[0])
                ok = len(parts) in (2, 3) and parts[1] in ("early", "late", "night")
            except (ValueError, IndexError):
                ok = False
            if not ok or len(parts) == 3 and not any(e.employee_id == parts[2] for e in state.employees):
                raise ToolError("Give the plan as YYYY-MM-DD/shift or YYYY-MM-DD/shift/employee id.")
        job = PrintJob(job_id=_next_id("print", [j.job_id for j in care.print_jobs]), kind=args.kind,
                       resident_id=args.resident_id, ref=ref, copies=args.copies, printer=args.printer,
                       submitted_at=_now(state), submitted_by=_user(), status="queued")
        care.print_jobs.append(job)
        return {"job": job.model_dump(mode="json")}


class PrintCancelArgs(ToolArgs):
    job_id: str


class PrintCancelTool(AppTool):
    app = "print"
    name = "print.cancel"
    description = "Cancel a print job that is still in the queue."
    args_model = PrintCancelArgs

    def execute(self, state: WorldState, args: PrintCancelArgs) -> dict[str, Any]:
        job = next((j for j in _ctx().care.print_jobs if j.job_id == args.job_id), None)
        if job is None:
            raise ToolError("Print job not found.", "NOT_FOUND")
        if job.status != "queued":
            raise ToolError("Only queued jobs can be cancelled.")
        job.status = "cancelled"
        return {"job": job.model_dump(mode="json")}


class PrintQueueTool(AppTool):
    app = "print"
    name = "print.queue"
    description = "List the print jobs of today and the printers of the home."
    args_model = NoArgs

    def execute(self, state: WorldState, args: NoArgs) -> dict[str, Any]:
        care = _ctx().care
        return {"printers": list(care.printers), "jobs": [j.model_dump(mode="json") for j in reversed(care.print_jobs)]}


# ---------------------------------------------------------------- rounds and notes to the head of nursing

POINT = re.compile(r"^(order_to_acknowledge|new_order|assessment_due:[a-z_]+|value_outside:[a-z0-9]+|wound|other)$")


class RoundPointArgs(ToolArgs):
    resident_id: str
    points: list[str] = Field(min_length=1, description="order_to_acknowledge, new_order, assessment_due:<scale id>, "
                                                        "value_outside:<observation kind>, wound or other")


class RoundArgs(ToolArgs):
    physician_id: str
    date: Day
    start: str = Field(pattern=HHMM)
    escort_id: str = Field(description="The nurse who goes round with the doctor (staff id).")
    residents: list[RoundPointArgs] = Field(min_length=1)


class RoundTool(AppTool):
    app = "care"
    name = "care.round_plan"
    description = ("Plan a doctor's round: which of the doctor's residents are seen, with which points to discuss, and "
                   "the nurse who accompanies the doctor. The app warns if that nurse is not on shift or not qualified.")
    args_model = RoundArgs

    def execute(self, state: WorldState, args: RoundArgs) -> dict[str, Any]:
        care = _ctx().care
        doc = care.physician(args.physician_id)
        if doc is None:
            raise ToolError("Doctor not found.", "NOT_FOUND")
        if not state.current_date <= args.date <= state.current_date + timedelta(days=7):
            raise ToolError("Rounds are planned for the next seven days.")
        points: dict[str, list[str]] = {}
        for item in args.residents:
            r = _resident(item.resident_id, present=True)
            if r.gp_id != doc.physician_id and doc.kind == "gp":
                raise ToolError(f"{r.name} is not a patient of this doctor.")
            bad = [p for p in item.points if not POINT.match(p)]
            if bad:
                raise ToolError("Unknown point: " + ", ".join(bad) + ".")
            points[r.resident_id] = sorted(set(points.get(r.resident_id, []) + item.points))
        staff = {s.employee_id: s for s in dailyplan.on_shift(state, care, *dailyplan.shift_at(
            datetime.combine(args.date, datetime.min.time()).replace(hour=int(args.start[:2]), minute=int(args.start[3:]))))}
        if not any(e.employee_id == args.escort_id for e in state.employees):
            raise ToolError("Employee not found.", "NOT_FOUND")
        warnings = []
        if args.escort_id not in staff:
            warnings.append("not_on_shift")
        elif Qualification.RN.value not in staff[args.escort_id].qualifications:
            warnings.append("qualification")
        rp = RoundPlan(round_id=_next_id("round", [x.round_id for x in care.rounds]), physician_id=doc.physician_id,
                       date=args.date, start=args.start, escort_id=args.escort_id, points=dict(sorted(points.items())),
                       created_at=_now(state), created_by=_user())
        care.rounds.append(rp)
        return {"round": rp.model_dump(mode="json"), "warnings": warnings}


class EscalateArgs(ToolArgs):
    subject: str = Field(min_length=3, max_length=160)
    text: str = Field(min_length=10, max_length=4000)
    resident_ids: list[str] = Field(default_factory=list)


class EscalateTool(AppTool):
    app = "care"
    name = "care.escalate"
    description = "Send a note to the head of nursing about something that cannot be done or needs her decision."
    args_model = EscalateArgs

    def execute(self, state: WorldState, args: EscalateArgs) -> dict[str, Any]:
        care = _ctx().care
        for rid in args.resident_ids:
            _resident(rid)
        note = Escalation(escalation_id=_next_id("note", [x.escalation_id for x in care.escalations]),
                          resident_ids=sorted(set(args.resident_ids)), subject=args.subject.strip(), text=args.text.strip(),
                          at=_now(state), by=_user())
        care.escalations.append(note)
        return {"note": note.model_dump(mode="json")}

class HandoverArgs(ToolArgs):
    ward_id: str
    shift: Literal["early", "late", "night"] = "early"
    text: str = Field(min_length=20, max_length=8000)
    resident_ids: list[str] = Field(default_factory=list, description="Residents the handover is about.")


class HandoverTool(AppTool):
    app = "care"
    name = "care.handover_write"
    description = "Write the handover of your shift to the next one: what is open, for whom, and why."
    args_model = HandoverArgs

    def execute(self, state: WorldState, args: HandoverArgs) -> dict[str, Any]:
        care, app = _ctx().care, _ctx().app
        if args.ward_id not in app.unit_names:
            raise ToolError("Unit not found.", "NOT_FOUND")
        for rid in args.resident_ids:
            _resident(rid)
        if not _meaningful(args.text):
            raise ToolError("Please write what the next shift needs to know.")
        h = Handover(handover_id=_next_id("handover", [x.handover_id for x in care.handovers]), ward_id=args.ward_id,
                     shift=args.shift, text=args.text.strip(), resident_ids=sorted(set(args.resident_ids)), written_at=_now(state),
                     by=_user())
        care.handovers.append(h)
        return {"handover": h.model_dump(mode="json")}


class WaitArgs(ToolArgs):
    until: str = Field(pattern=HHMM, description="Clock time to wait for (HH:MM, today).")


class WaitTool(AppTool):
    app = "plan"
    name = "shift.wait"
    description = ("Wait until a clock time today (for example for a resident who is expected back). Colleagues go on "
                   "working their plan meanwhile; mail and chat that arrive in the meantime are there afterwards.")
    args_model = WaitArgs

    def execute(self, state: WorldState, args: WaitArgs) -> dict[str, Any]:
        care = _ctx().care
        if care.clock_start is None:
            raise ToolError("This function is not available here.", "UNKNOWN_TOOL")
        now = _when(_now(state))
        target = datetime.combine(state.current_date, datetime.min.time()).replace(hour=int(args.until[:2]), minute=int(args.until[3:]))
        care.waited_min += max(0, int((target - now).total_seconds() // 60))
        _ctx().app._clock_base = care.clock_start + care.waited_min
        return {"now": _now(state)}


# ---------------------------------------------------------------- helpers shared with the GUI

def age(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def latest_assessments(care: CareState, resident_id: str) -> dict[str, AssessmentRecord]:
    out: dict[str, AssessmentRecord] = {}
    for a in sorted((a for a in care.assessments if a.resident_id == resident_id),
                    key=lambda a: (a.performed_at, a.assessment_id)):
        out[a.scale_id] = a
    return out


def current_shift(state: WorldState) -> tuple[date, Any]:
    ctx = _CURRENT.get()
    now = _when(ctx.app.now(state.current_date)) if ctx else datetime.combine(state.current_date, datetime.min.time())
    return dailyplan.shift_at(now)


CARE_TOOLS: tuple[AppTool, ...] = (
    ResidentsTool(), RecordTool(), OrdersTool(), HouseStandardTool(), ScalesTool(), PlanViewTool(),
    ObservationTool(), FluidTool(), ExcretionTool(), AssessmentTool(), RiskTool(), PlanEntryTool(),
    PlanEntryUpdateTool(), MeasureTool(), MeasureEndTool(), ReportTool(), AcknowledgeTool(), AppointmentTool(),
    AppointmentCancelTool(), AssignTool(), StatusTool(), AddItemTool(), AdmitTool(), ContactTool(), AllergyTool(),
    ResidentStatusTool(), WoundTool(), WoundAssessTool(), AttachTool(), TransferTool(), PrintTool(), PrintCancelTool(),
    PrintQueueTool(), RoundTool(), EscalateTool(), HandoverTool(), WaitTool(),
)
CARE_TOOLS_BY_NAME = {t.name: t for t in CARE_TOOLS}
CARE_READ_ONLY = frozenset({"care.residents", "care.record", "care.orders", "care.house_standard", "care.scales",
                            "plan.view", "print.queue"})
