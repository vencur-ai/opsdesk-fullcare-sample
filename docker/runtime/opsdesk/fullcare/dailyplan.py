"""Daily plan: one task list per staff member and shift, computed from measures, orders and appointments.

Items are a pure function of the care state and the date (`items_for`), with a stable key per item, so the GUI,
the API and the verifier see the same list. What the nurse did with an item (assigned to whom, done or not) is kept
in `CareState.plan_state` under that key. Staff on shift come from the rota (`on_shift`); an item may go only to
someone on that shift with its qualification and skills. Problems are warnings, never blocks (`warnings`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from opsdesk.core.models import Qualification, ShiftType
from opsdesk.core.state import WorldState
from opsdesk.core.time_utils import SHIFT_TIMES
from opsdesk.fullcare.models import CareState, Measure, PlanItemState, Schedule

PLAN_SHIFTS = (ShiftType.EARLY, ShiftType.LATE, ShiftType.NIGHT)


@dataclass(frozen=True)
class PlanItem:
    item_key: str
    date: date  # the shift's date (a night item at 02:00 belongs to the night shift of the day before)
    shift: str
    ward_id: str
    resident_id: str
    due: datetime
    label: str
    catalogue_key: str
    duration_min: int
    qualification: Qualification
    skills: tuple[str, ...]
    source_kind: str  # "measure", "order", "appointment", "extra"
    source_id: str
    assigned_to: str | None = None
    status: str = "open"
    reason: str = ""

    def as_dict(self) -> dict:
        return {"item_key": self.item_key, "date": self.date.isoformat(), "shift": self.shift, "ward_id": self.ward_id,
                "resident_id": self.resident_id, "due": self.due.strftime("%Y-%m-%d %H:%M"), "label": self.label,
                "catalogue_key": self.catalogue_key, "duration_min": self.duration_min,
                "qualification": self.qualification.value, "skills": list(self.skills),
                "source_kind": self.source_kind, "source_id": self.source_id, "assigned_to": self.assigned_to,
                "status": self.status, "reason": self.reason}


@dataclass
class StaffOnShift:
    employee_id: str
    name: str
    qualifications: tuple[str, ...]
    skills: tuple[str, ...]
    assignment_id: str
    load_min: int = 0
    items: list[str] = field(default_factory=list)


def shift_bounds(day: date, shift: ShiftType | str) -> tuple[datetime, datetime]:
    st = SHIFT_TIMES[ShiftType(shift)]
    start, end = datetime.combine(day, st.start), datetime.combine(day, st.end)
    return start, end + timedelta(days=1) if end <= start else end


def shift_at(moment: datetime) -> tuple[date, ShiftType]:
    """Which plan shift a moment falls in (early 06–14, late 14–22, night 22–06 of the day it started)."""
    for day in (moment.date(), moment.date() - timedelta(days=1)):
        for shift in PLAN_SHIFTS:
            start, end = shift_bounds(day, shift)
            if start <= moment < end:
                return day, shift
    raise ValueError("no shift")  # the three shifts cover the whole day


def _hm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


def due_times(schedule: Schedule, start: datetime, end: datetime) -> list[datetime]:
    """Due moments of a schedule inside [start, end)."""
    out: list[datetime] = []
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        if schedule.kind in ("times", "weekly"):
            if schedule.kind == "times" or day.weekday() in schedule.weekdays:
                out += [datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m)
                        for h, m in map(_hm, schedule.times)]
        else:
            ah, am = _hm(schedule.anchor)
            moment = datetime.combine(day, datetime.min.time()).replace(hour=ah, minute=am)
            stop = moment + timedelta(days=1)
            while moment < stop:
                out.append(moment)
                night = moment.hour >= 22 or moment.hour < 6
                step = (schedule.night_every_hours if night and schedule.night_every_hours else schedule.every_hours) or 24
                moment += timedelta(hours=step)
        day += timedelta(days=1)
    return sorted({m for m in out if start <= m < end})


def _active(measure: Measure, day: date) -> bool:
    return measure.start <= day and (measure.end is None or day <= measure.end)


def items_for(care: CareState, day: date, shift: ShiftType | str, ward_id: str | None = None) -> list[PlanItem]:
    """Every item of one shift (all wards, or one), with what the nurse recorded for it, in a stable order."""
    shift = ShiftType(shift)
    start, end = shift_bounds(day, shift)
    present = {r.resident_id: r for r in care.residents if r.status == "present"
               and (ward_id is None or r.ward_id == ward_id)}
    states = {s.item_key: s for s in care.plan_state}
    raw: list[PlanItem] = []
    for m in care.measures:
        resident = present.get(m.resident_id)
        if resident is None or not (_active(m, start.date()) or _active(m, (end - timedelta(minutes=1)).date())):
            continue
        for due in due_times(m.schedule, start, end):
            if not _active(m, due.date()):
                continue
            raw.append(PlanItem(f"{m.measure_id}@{due:%Y-%m-%dT%H:%M}", day, shift.value, resident.ward_id,
                                m.resident_id, due, m.label, m.catalogue_key, m.duration_min, m.qualification,
                                tuple(m.skills), "order" if m.order_id else "measure", m.order_id or m.measure_id))
    escort = care.standard.entry("round_escort")
    for a in care.appointments:
        resident = present.get(a.resident_id)
        if resident is None or a.status != "planned" or a.date != day and a.date != end.date():
            continue
        due = datetime.combine(a.date, datetime.min.time()).replace(hour=_hm(a.start)[0], minute=_hm(a.start)[1])
        if a.escort_qualification is not None and start <= due < end and escort is not None:
            raw.append(PlanItem(f"{a.appointment_id}@{due:%Y-%m-%dT%H:%M}", day, shift.value, resident.ward_id,
                                a.resident_id, due, f"{escort.label.get('en-GB')}: {a.place}", "round_escort",
                                escort.duration_min, a.escort_qualification, (), "appointment", a.appointment_id))
    for rp in care.rounds:  # the doctor's round itself (0.6.1, head of nursing H66): one line for the nurse who goes round
        due = datetime.combine(rp.date, datetime.min.time()).replace(hour=_hm(rp.start)[0], minute=_hm(rp.start)[1])
        wards = sorted({present[rid].ward_id for rid in rp.points if rid in present})
        if rp.date != day or not wards or not (start <= due < end):
            continue
        raw.append(PlanItem(f"{rp.round_id}@{due:%Y-%m-%dT%H:%M}", day, shift.value, wards[0], "", due, "GP round",
                            "gp_round", care.standard.gp_round_minutes, Qualification.RN, (), "round", rp.round_id,
                            assigned_to=rp.escort_id))
    for x in care.plan_extra:
        resident = present.get(x.resident_id)
        due = datetime.combine(x.date, datetime.min.time()).replace(hour=_hm(x.due)[0], minute=_hm(x.due)[1])
        if resident is None or not (start <= due < end):
            continue
        raw.append(PlanItem(f"{x.item_id}@{due:%Y-%m-%dT%H:%M}", day, shift.value, resident.ward_id, x.resident_id,
                            due, x.label, x.catalogue_key, x.duration_min, x.qualification, tuple(x.skills), "extra",
                            x.item_id))
    out = []
    for item in raw:
        s = states.get(item.item_key)
        if s is not None:
            item = PlanItem(**{**item.__dict__, "assigned_to": s.assigned_to or item.assigned_to, "status": s.status,
                               "reason": s.reason})
        out.append(item)
    return sorted(out, key=lambda i: (i.due, i.ward_id, i.resident_id, i.item_key))


def on_shift(world: WorldState, care: CareState, day: date, shift: ShiftType | str,
             ward_id: str | None = None) -> list[StaffOnShift]:
    """Staff the rota has on this shift (and ward), minus anyone absent that day."""
    shift = ShiftType(shift)
    absent = {a.employee_id for a in world.absences if a.start_date <= day <= a.end_date}
    by_id = {e.employee_id: e for e in world.employees}
    out = []
    for a in sorted(world.roster.assignments, key=lambda x: (x.employee_id, x.assignment_id)):
        if a.date != day or a.shift_type != shift or (ward_id and a.ward_id != ward_id) or a.employee_id in absent:
            continue
        e = by_id.get(a.employee_id)
        if e is None:
            continue
        out.append(StaffOnShift(e.employee_id, e.name, tuple(q.value for q in e.qualifications),
                                tuple(care.staff_skills.get(e.employee_id, [])), a.assignment_id))
    return out


def eligibility(item: PlanItem, staff: StaffOnShift | None) -> list[str]:
    """Why this person may not do this item (empty: may). Codes, shown as text by the GUI."""
    if staff is None:
        return ["not_on_shift"]
    reasons = []
    if item.qualification.value not in staff.qualifications:
        reasons.append("qualification")
    missing = [s for s in item.skills if s not in staff.skills]
    if missing:
        reasons.append("skill:" + ",".join(missing))
    return reasons


def warnings(world: WorldState, care: CareState, day: date, shift: ShiftType | str,
             ward_id: str | None = None) -> dict[str, list[str]]:
    """Per item key: not assigned, assignee not on shift / not qualified, two items at once, over shift length."""
    items = items_for(care, day, shift, ward_id)
    staff = {s.employee_id: s for s in on_shift(world, care, day, shift, ward_id)}
    start, end = shift_bounds(day, shift)
    out: dict[str, list[str]] = {}
    load: dict[str, int] = {}
    seen: dict[tuple[str, datetime], str] = {}
    for item in items:
        if item.status in ("done", "not_done", "handed_over"):
            continue
        if item.assigned_to is None:
            out.setdefault(item.item_key, []).append("unassigned")
            continue
        for reason in eligibility(item, staff.get(item.assigned_to)):
            out.setdefault(item.item_key, []).append(reason)
        load[item.assigned_to] = load.get(item.assigned_to, 0) + item.duration_min
        clash = seen.get((item.assigned_to, item.due))
        if clash is not None:
            out.setdefault(item.item_key, []).append("same_time:" + clash)
        seen[(item.assigned_to, item.due)] = item.item_key
    minutes = int((end - start).total_seconds() // 60) - SHIFT_TIMES[ShiftType(shift)].break_minutes
    for employee_id, total in sorted(load.items()):
        if total > minutes:
            for item in items:
                if item.assigned_to == employee_id:
                    out.setdefault(item.item_key, []).append("over_shift_length")
    return out


def set_state(care: CareState, item_key: str, *, at: str, **changes) -> PlanItemState:
    current = next((s for s in care.plan_state if s.item_key == item_key), None)
    if current is None:
        current = PlanItemState(item_key=item_key)
        care.plan_state.append(current)
        care.plan_state.sort(key=lambda s: s.item_key)
        current = next(s for s in care.plan_state if s.item_key == item_key)
    for key, value in changes.items():
        setattr(current, key, value)
    current.changed_at = at
    return current
