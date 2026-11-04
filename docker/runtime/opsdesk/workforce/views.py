"""Pure read functions shared by the GUI pages and the workforce read tools.

Nothing here changes state for longer than one call: candidate checks add a trial assignment and
remove it again under the session lock.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from opsdesk.apps.roster import roster_fingerprint
from opsdesk.core.models import AgencyRequestStatus, Assignment, Qualification, ShiftType
from opsdesk.core.state import WorldState
from opsdesk.core.time_utils import SHIFT_TIMES, shift_interval, shift_work_minutes  # noqa: F401 (re-exported)
from opsdesk.rules.engine import check_world_rules
from opsdesk.solver.cpsat import (
    compute_cost_breakdown,
    coverage_gaps,
    coverage_position_count,
    employee_shift_cost_cents,
)

SHIFTS = (ShiftType.EARLY, ShiftType.LATE, ShiftType.NIGHT)
QUAL_ORDER = (Qualification.RN, Qualification.TL, Qualification.CA)


def horizon_dates(state: WorldState) -> list[date]:
    return [state.start_date + timedelta(days=i) for i in range(state.horizon_days)]


def gaps(state: WorldState):
    return coverage_gaps(state, state.roster.assignments, state.agency_requests)


def open_positions(state: WorldState) -> list[dict[str, Any]]:
    """Uncovered demand in the verifier's own terms (same function, same order)."""
    out = []
    for gap in gaps(state):
        out.append({
            "date": gap.date, "unit": gap.ward_id, "shift": gap.shift_type,
            "missing_headcount": gap.missing_headcount,
            "missing_qualifications": {q.value: n for q, n in gap.missing_qualifications.items() if n > 0},
            # One claim item per shift, named exactly as the verifier names it (score._report_uncovered_v2).
            "missing": (sorted(gap.missing_qualifications, key=lambda q: q.value)[0].value
                        if gap.missing_qualifications else "headcount"),
        })
    return out


def covered_positions(state: WorldState) -> int:
    return coverage_position_count(state, gaps(state))


def total_cost_cents(state: WorldState) -> int:
    return sum(compute_cost_breakdown(state, state.roster.assignments, state.agency_requests).values())


def agency_spend_cents(state: WorldState) -> int:
    confirmed = [r for r in state.agency_requests if r.status == AgencyRequestStatus.CONFIRMED]
    return sum(compute_cost_breakdown(state, [], confirmed).values())


def numbers(state: WorldState) -> dict[str, Any]:
    return {"covered_positions": covered_positions(state), "open_positions": len(gaps(state)),
            "total_cost_cents": total_cost_cents(state), "agency_spend_cents": agency_spend_cents(state),
            "agency_budget_cents": int(state.agency_budget * 100), "roster_fingerprint": roster_fingerprint(state)}


def shift_cost_cents(state: WorldState, assignment: Assignment) -> int:
    employee = next(e for e in state.employees if e.employee_id == assignment.employee_id)
    return employee_shift_cost_cents(employee.hourly_rate, shift_work_minutes(assignment), assignment.shift_type)


def warnings(state: WorldState) -> list[dict[str, Any]]:
    """Hard-rule findings of the active rule pack, as warnings (they never block an edit)."""
    out = []
    for item in check_world_rules(state):
        if item.kind.value != "hard":
            continue
        out.append({"rule_id": item.rule_id, "staff_id": item.staff_id, "date": item.occurred_on,
                    "message": item.message, "entity_ids": list(item.entity_ids)})
    return sorted(out, key=lambda w: (w["date"] or date.min, w["rule_id"], w["staff_id"], w["message"]))


def grid(state: WorldState, dates: list[date], unit: str | None) -> dict[tuple, dict[str, Any]]:
    """Cell model keyed by (date, ward_id, shift)."""
    demand = {(s.date, s.ward_id, s.shift_type): s for s in state.demand_slots}
    by_cell: dict[tuple, list[Assignment]] = defaultdict(list)
    for a in state.roster.assignments:
        by_cell[(a.date, a.ward_id, a.shift_type)].append(a)
    open_by_cell = {(g["date"], g["unit"], g["shift"]): g for g in open_positions(state)}
    agency_by_cell: dict[tuple, int] = defaultdict(int)
    for r in state.agency_requests:
        if r.status == AgencyRequestStatus.CONFIRMED:
            agency_by_cell[(r.requested_for, r.ward_id, r.shift_type)] += r.staff_count
    warn_by_staff_day: dict[tuple, list[dict]] = defaultdict(list)
    for w in warnings(state):
        if w["date"] is not None:
            warn_by_staff_day[(w["staff_id"], w["date"])].append(w)
    names = {e.employee_id: e for e in state.employees}
    cells = {}
    for d in dates:
        for ward in state.wards:
            if unit and ward.ward_id != unit:
                continue
            for shift in SHIFTS:
                key = (d, ward.ward_id, shift)
                slot = demand.get(key)
                staff = sorted(by_cell.get(key, []), key=lambda a: (names[a.employee_id].name, a.assignment_id))
                cells[key] = {
                    "assignments": staff,
                    "demand": slot,
                    "open": open_by_cell.get(key),
                    "agency": agency_by_cell.get(key, 0),
                    "warnings": [w for a in staff for w in warn_by_staff_day.get((a.employee_id, d), [])],
                }
    return cells


def candidates(state: WorldState, work_date: date, ward_id: str, shift: ShiftType) -> list[dict[str, Any]]:
    """Every employee with why they can or cannot take this shift (trial assignment, then removed)."""
    trial = Assignment(assignment_id="assignment-trial", employee_id="trial", date=work_date,
                       ward_id=ward_id, shift_type=shift)
    start, end = shift_interval(trial)
    before = {(v["rule_id"], v["staff_id"], v["date"], v["message"]) for v in warnings(state)}
    rows = []
    for employee in sorted(state.employees, key=lambda e: (e.name, e.employee_id)):
        row = {"employee_id": employee.employee_id, "name": employee.name,
               "qualifications": sorted(q.value for q in employee.qualifications), "status": "available",
               "reasons": []}
        absence = next((a for a in state.absences if a.employee_id == employee.employee_id
                        and a.start_date <= work_date <= a.end_date), None)
        busy = next((a for a in state.roster.assignments if a.employee_id == employee.employee_id
                     and shift_interval(a)[0] < end and shift_interval(a)[1] > start), None)
        on_shift = next((a for a in state.roster.assignments if a.employee_id == employee.employee_id
                         and a.date == work_date and a.ward_id == ward_id and a.shift_type == shift), None)
        if on_shift:
            row["status"] = "on_shift"
        elif absence:
            row["status"] = "absent"
            row["reasons"].append({"kind": "absence", "absence_type": absence.absence_type.value,
                                   "until": absence.end_date, "absence_id": absence.absence_id})
        elif busy:
            row["status"] = "busy"
            row["reasons"].append({"kind": "overlap", "shift": busy.shift_type.value, "date": busy.date,
                                   "unit": busy.ward_id})
        else:
            probe = trial.model_copy(update={"employee_id": employee.employee_id})
            state.roster.assignments.append(probe)
            try:
                new = [v for v in warnings(state) if v["staff_id"] == employee.employee_id
                       and (v["rule_id"], v["staff_id"], v["date"], v["message"]) not in before]
            finally:
                state.roster.assignments.remove(probe)
            if new:
                row["status"] = "warning"
                row["reasons"] += [{"kind": "rule", "rule_id": v["rule_id"], "message": v["message"]} for v in new]
        rows.append(row)
    order = {"available": 0, "warning": 1, "busy": 2, "absent": 3, "on_shift": 4}
    return sorted(rows, key=lambda r: (order[r["status"]], r["name"], r["employee_id"]))


def planned_minutes(state: WorldState, employee_id: str) -> int:
    return sum(shift_work_minutes(a) for a in state.roster.assignments if a.employee_id == employee_id)


def shift_times(shift: ShiftType) -> tuple[str, str]:
    t = SHIFT_TIMES[shift]
    return t.start.strftime("%H:%M"), t.end.strftime("%H:%M")
