"""Workforce-mode tools: the extra domain functions the replica's screens need.

Available only in workforce sessions (`task.json` "surface": "workforce"); the 2.4 registry and its
19 tools are unchanged. Each tool is one `AppTool`, so GUI and API calls are validated, executed and
logged the same way. The app-side records live in a `WorkforceState` bound for the call.
"""

from __future__ import annotations

import contextvars
from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from opsdesk.apps.base import AppTool, ToolArgs, ToolError, to_json_data
from opsdesk.apps.ids import next_monotonic_id
from opsdesk.apps.roster import SwapShiftsArgs, SwapShiftsTool, roster_fingerprint
from opsdesk.core.models import Absence, AbsenceType, AgencyRequestStatus, Qualification, ShiftType
from opsdesk.core.state import WorldState
from opsdesk.workforce import views
from opsdesk.workforce.state import UnderstaffingItem, UnderstaffingReport, WorkforceState

_CURRENT: contextvars.ContextVar[WorkforceState | None] = contextvars.ContextVar("workforce_state", default=None)


def bind(app: WorkforceState):
    return _CURRENT.set(app)


def unbind(token) -> None:
    _CURRENT.reset(token)


def _app() -> WorkforceState:
    app = _CURRENT.get()
    if app is None:
        raise ToolError("This function is not available here.", "UNKNOWN_TOOL")
    return app


def _employee(state: WorldState, employee_id: str):
    employee = next((e for e in state.employees if e.employee_id == employee_id), None)
    if employee is None:
        raise ToolError("Employee not found.")
    return employee


def _inside(state: WorldState, day: date) -> bool:
    return 0 <= (day - state.start_date).days < state.horizon_days


UNCLEAR_REASON = "Please give the reason in a few words (the person reads it)."


def meaningful(text: str) -> bool:
    """A reason a person can read: at least two words and eight letters (3.1.1; "." or "x" are refused)."""
    import re as _re
    return len(_re.findall(r"[^\W\d_]", text)) >= 8 and len(text.split()) >= 2


# ---------------------------------------------------------------- requests

class ListRequestsArgs(ToolArgs):
    status: Literal["open", "approved", "declined", "withdrawn", "all"] = "all"


class ListRequestsTool(AppTool):
    app = "requests"
    name = "requests.list"
    description = ("List shift swap requests from staff with both shifts (date, unit, shift, person), their "
                   "status (open, approved, declined, withdrawn) and whether the swap partner has confirmed.")
    args_model = ListRequestsArgs

    def execute(self, state: WorldState, args: ListRequestsArgs) -> list[dict[str, Any]]:
        by_id = {a.assignment_id: a for a in state.roster.assignments}
        out = []
        for req in _app().requests:
            if args.status != "all" and req.status != args.status:
                continue
            row = req.model_dump(mode="json")
            for key in ("first_assignment_id", "second_assignment_id"):
                a = by_id.get(getattr(req, key))
                row[key.replace("_id", "")] = to_json_data(a) if a else None
            out.append(row)
        return out


class DecideRequestArgs(ToolArgs):
    request_id: str = Field(min_length=1)
    decision: Literal["approve", "decline"]
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def reason_for_decline(self) -> DecideRequestArgs:
        if self.decision == "decline" and not self.reason.strip():
            raise ValueError("a reason is required to decline a request")
        if self.decision == "decline" and not meaningful(self.reason):
            raise ValueError(UNCLEAR_REASON)
        return self


class DecideRequestTool(AppTool):
    app = "requests"
    name = "requests.decide"
    description = ("Approve or decline one swap request. Approving swaps the two shifts (both people must accept); "
                   "declining needs a reason. Staff are not messaged automatically.")
    args_model = DecideRequestArgs

    def execute(self, state: WorldState, args: DecideRequestArgs) -> dict[str, Any]:
        app = _app()
        req = next((r for r in app.requests if r.request_id == args.request_id), None)
        if req is None:
            raise ToolError("Request not found.")
        if req.status == "withdrawn":
            raise ToolError("This request was withdrawn by the employee.")
        if req.status != "open":
            raise ToolError("This request has already been decided.")
        if args.decision == "approve":
            if not req.partner_confirmed:
                raise ToolError("The swap partner has not confirmed this swap yet.")
            SwapShiftsTool().execute(state, SwapShiftsArgs(first_assignment_id=req.first_assignment_id,
                                                           second_assignment_id=req.second_assignment_id))
            req.status = "approved"
        else:
            req.status = "declined"
        req.reason = args.reason.strip()
        req.decided_on = state.current_date
        return {"request_id": req.request_id, "status": req.status, "reason": req.reason}


# ---------------------------------------------------------------- absences

class RecordAbsenceArgs(ToolArgs):
    employee_id: str = Field(min_length=1)
    absence_type: AbsenceType
    start_date: date
    end_date: date
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def ordered(self) -> RecordAbsenceArgs:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class RecordAbsenceTool(AppTool):
    app = "absences"
    name = "absences.record"
    description = "Record a sick leave, holiday or other absence for one employee."
    args_model = RecordAbsenceArgs

    def execute(self, state: WorldState, args: RecordAbsenceArgs) -> dict[str, Any]:
        from opsdesk.workforce.state import AbsenceMeta
        _employee(state, args.employee_id)
        absence = Absence(absence_id=next_monotonic_id("absence", [a.absence_id for a in state.absences]),
                          employee_id=args.employee_id, absence_type=args.absence_type,
                          start_date=args.start_date, end_date=args.end_date)
        state.absences.append(absence)
        app = _app()
        app.absence_meta.append(AbsenceMeta(absence_id=absence.absence_id, entered_by=app.home.user_name,
                                            entered_on=state.current_date, channel="office", note=args.note))
        return {"absence": to_json_data(absence)}


class UpdateAbsenceArgs(ToolArgs):
    absence_id: str = Field(min_length=1)
    absence_type: AbsenceType | None = None
    start_date: date | None = None
    end_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


class UpdateAbsenceTool(AppTool):
    app = "absences"
    name = "absences.update"
    description = "Change the type, dates or note of an existing absence."
    args_model = UpdateAbsenceArgs

    def execute(self, state: WorldState, args: UpdateAbsenceArgs) -> dict[str, Any]:
        absence = next((a for a in state.absences if a.absence_id == args.absence_id), None)
        if absence is None:
            raise ToolError("Absence not found.")
        start = args.start_date or absence.start_date
        end = args.end_date or absence.end_date
        if end < start:
            raise ToolError("The end date is before the start date.")
        index = state.absences.index(absence)
        state.absences[index] = absence.model_copy(update={
            "absence_type": args.absence_type or absence.absence_type, "start_date": start, "end_date": end})
        if args.note is not None:
            meta = next((m for m in _app().absence_meta if m.absence_id == args.absence_id), None)
            if meta is not None:
                meta.note = args.note
        return {"absence": to_json_data(state.absences[index])}


class DeleteAbsenceArgs(ToolArgs):
    absence_id: str = Field(min_length=1)


class DeleteAbsenceTool(AppTool):
    app = "absences"
    name = "absences.delete"
    description = "Delete an absence entry."
    args_model = DeleteAbsenceArgs

    def execute(self, state: WorldState, args: DeleteAbsenceArgs) -> dict[str, Any]:
        absence = next((a for a in state.absences if a.absence_id == args.absence_id), None)
        if absence is None:
            raise ToolError("Absence not found.")
        state.absences.remove(absence)
        return {"deleted_absence_id": absence.absence_id}


# ---------------------------------------------------------------- staff records

class UpdateEmployeeArgs(ToolArgs):
    employee_id: str = Field(min_length=1)
    phone: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)
    contract_hours_per_week: float | None = Field(default=None, gt=0, le=60)
    qualifications: list[Qualification] | None = Field(default=None, min_length=1)


class UpdateEmployeeTool(AppTool):
    app = "hr"
    name = "hr.update_employee"
    description = "Edit an employee's phone number, notes, contract hours or qualifications."
    args_model = UpdateEmployeeArgs

    def execute(self, state: WorldState, args: UpdateEmployeeArgs) -> dict[str, Any]:
        employee = _employee(state, args.employee_id)
        profile = _app().profile(args.employee_id)
        if args.phone is not None and profile is not None:
            profile.phone = args.phone
        if args.notes is not None and profile is not None:
            profile.notes = args.notes
        index = state.employees.index(employee)
        update: dict[str, Any] = {}
        if args.contract_hours_per_week is not None:
            update["contract"] = employee.contract.model_copy(update={"hours_per_week": args.contract_hours_per_week})
        if args.qualifications is not None:
            update["qualifications"] = set(args.qualifications)
        if update:
            state.employees[index] = employee.model_copy(update=update)
        return {"employee_id": args.employee_id, "updated": sorted(k for k, v in args.model_dump().items()
                                                                   if v is not None and k != "employee_id")}


# ---------------------------------------------------------------- agency

class CancelAgencyArgs(ToolArgs):
    request_id: str = Field(min_length=1)


class CancelAgencyTool(AppTool):
    app = "agency"
    name = "agency.cancel_request"
    description = "Cancel a confirmed agency booking."
    args_model = CancelAgencyArgs

    def execute(self, state: WorldState, args: CancelAgencyArgs) -> dict[str, Any]:
        request = next((r for r in state.agency_requests if r.request_id == args.request_id), None)
        if request is None:
            raise ToolError("Agency booking not found.")
        if request.status == AgencyRequestStatus.CANCELLED:
            raise ToolError("This booking is already cancelled.")
        index = state.agency_requests.index(request)
        state.agency_requests[index] = request.model_copy(update={"status": AgencyRequestStatus.CANCELLED})
        return {"request_id": request.request_id, "status": "cancelled"}


# ---------------------------------------------------------------- rota helpers and reports

class CandidatesArgs(ToolArgs):
    date: date
    ward_id: str = Field(min_length=1)
    shift_type: ShiftType


class CandidatesTool(AppTool):
    app = "roster"
    name = "roster.candidates"
    description = ("For one shift, list every employee with whether they can take it and why not "
                   "(absence, another shift, rule warnings the change would cause).")
    args_model = CandidatesArgs

    def execute(self, state: WorldState, args: CandidatesArgs) -> list[dict[str, Any]]:
        if not any(w.ward_id == args.ward_id for w in state.wards):
            raise ToolError("Ward not found.")
        if not _inside(state, args.date):
            raise ToolError("Shift date is outside the planning period.")
        return views.candidates(state, args.date, args.ward_id, args.shift_type)


class UnderstaffingItemArgs(ToolArgs):
    date: date
    ward_id: str = Field(min_length=1)
    shift_type: ShiftType
    missing: Literal["registered_nurse", "care_assistant", "team_lead", "headcount"]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def reason_in_words(self) -> UnderstaffingItemArgs:
        if not meaningful(self.reason):
            raise ValueError(UNCLEAR_REASON)
        return self


class ReportUnderstaffingArgs(ToolArgs):
    shifts: list[UnderstaffingItemArgs] = Field(min_length=1, max_length=400)
    note: str = Field(default="", max_length=2000)


class ReportUnderstaffingTool(AppTool):
    app = "reports"
    name = "reports.understaffing"
    description = ("Report to the home manager which shifts cannot be covered and why (one entry per shift, "
                   "naming what is missing). A new report replaces the previous one.")
    args_model = ReportUnderstaffingArgs

    def execute(self, state: WorldState, args: ReportUnderstaffingArgs) -> dict[str, Any]:
        app = _app()
        for item in args.shifts:
            if not any(w.ward_id == item.ward_id for w in state.wards):
                raise ToolError("Ward not found.")
            if not _inside(state, item.date):
                raise ToolError("Shift date is outside the planning period.")
        keys = [(i.date, i.ward_id, i.shift_type) for i in args.shifts]
        if len(set(keys)) != len(keys):
            raise ToolError("Each shift can be listed only once.")
        for previous in app.reports:
            previous.withdrawn = True
        numbers = views.numbers(state)
        report = UnderstaffingReport(
            report_id=next_monotonic_id("report", [r.report_id for r in app.reports]),
            sent_on=state.current_date,
            items=[UnderstaffingItem(date=i.date, unit=i.ward_id, shift=i.shift_type, missing=i.missing,
                                     reason=i.reason.strip()) for i in sorted(args.shifts, key=lambda i: (
                                         i.date, i.ward_id, i.shift_type.value))],
            note=args.note, covered_positions=numbers["covered_positions"],
            total_cost_cents=numbers["total_cost_cents"], agency_spend_cents=numbers["agency_spend_cents"],
            roster_fingerprint=numbers["roster_fingerprint"])
        app.reports.append(report)
        return {"report_id": report.report_id, "shifts": len(report.items)}


class WithdrawReportArgs(ToolArgs):
    report_id: str = Field(min_length=1)


class WithdrawReportTool(AppTool):
    app = "reports"
    name = "reports.withdraw_understaffing"
    description = "Withdraw an understaffing report sent earlier (for example after the gaps were filled)."
    args_model = WithdrawReportArgs

    def execute(self, state: WorldState, args: WithdrawReportArgs) -> dict[str, Any]:
        report = next((r for r in _app().reports if r.report_id == args.report_id), None)
        if report is None:
            raise ToolError("Report not found.")
        if report.withdrawn:
            raise ToolError("This report is no longer active.")
        report.withdrawn = True
        return {"report_id": report.report_id, "withdrawn": True}


# ---------------------------------------------------------------- payroll (3.1.1)

from opsdesk.apps.payroll import ExportPayrollArgs, ExportPayrollTool, _next_export_id  # noqa: E402


class WorkforcePayrollExportTool(ExportPayrollTool):
    """The payroll export as the app shows it: clocked time is the terminal's in/out punches minus the shift's
    break (as rostered working minutes are), so the difference per person is the raw sum over all shifts – the
    payroll office's tolerance per shift is applied by the planner, not by this export."""
    description = ("Export rostered and clocked working minutes per employee for a period (clocked = punches minus "
                   "the break) with the raw difference.")

    def execute(self, state: WorldState, args: ExportPayrollArgs) -> dict[str, Any]:
        from opsdesk.core.models import PayrollExport, PayrollRow
        from opsdesk.core.time_utils import shift_break_minutes, shift_work_minutes
        shifts = [a for a in state.roster.assignments if args.start_date <= a.date <= args.end_date]
        breaks = {(a.employee_id, a.date): shift_break_minutes(a) for a in shifts}
        rows = []
        for employee in sorted(state.employees, key=lambda e: e.employee_id):
            rostered = sum(shift_work_minutes(a) for a in shifts if a.employee_id == employee.employee_id)
            clocked = 0
            for entry in state.timeclock_entries:
                if entry.employee_id == employee.employee_id and args.start_date <= entry.shift_date <= args.end_date:
                    length = int((entry.clock_out - entry.clock_in).total_seconds() // 60)
                    clocked += length - breaks.get((entry.employee_id, entry.shift_date), 30 if length > 6 * 60 else 0)
            rows.append(PayrollRow(employee_id=employee.employee_id, rostered_minutes=rostered, clocked_minutes=clocked,
                                   payable_minutes=clocked, discrepancy_minutes=clocked - rostered))
        export = PayrollExport(export_id=_next_export_id(state), start_date=args.start_date, end_date=args.end_date,
                               created_on=state.current_date, rows=rows)
        state.payroll_exports.append(export)
        return to_json_data(export)


# ---------------------------------------------------------------- shift notices (3.1.1)

from opsdesk.apps.messaging import SendMessageArgs, SendMessageTool  # noqa: E402


class WorkforceSendMessageTool(SendMessageTool):
    """A message with a shift attached carries the rota's own line under the text – whether the person is on that
    shift now – the way rota software sends shift notices, so the notice says what really changed."""
    description = ("Message one staff member. With a shift (date, unit, shift) it is a shift notice: the rota adds a "
                   "line saying whether they are on that shift now. Shift requests get a reply from the person.")

    def execute(self, state: WorldState, args: SendMessageArgs) -> dict[str, Any]:
        if args.shift_date is not None and not args.request_intent:
            from opsdesk.workforce.i18n import fmt_short, shift_name
            app = _app()
            loc = app.locale if app else "en-GB"
            on = any(a.employee_id == args.recipient_id and a.date == args.shift_date and a.ward_id == args.ward_id
                     and a.shift_type == args.shift_type for a in state.roster.assignments)
            unit = app.unit_names.get(args.ward_id, args.ward_id) if app else args.ward_id
            what = f"{fmt_short(loc, args.shift_date)} · {unit} · {shift_name(loc, args.shift_type.value)}"
            line = ((f"Dienstplan: {what} – du bist eingetragen." if on else f"Dienstplan: {what} – du bist nicht mehr eingetragen.")
                    if loc == "de-DE" else
                    (f"Rota: {what} – you are on this shift." if on else f"Rota: {what} – you are no longer on this shift."))
            args = args.model_copy(update={"body": (args.body.rstrip() + "\n\n— " + line)[:4000]})
        return super().execute(state, args)


WORKFORCE_TOOLS: tuple[AppTool, ...] = (
    ListRequestsTool(), DecideRequestTool(), RecordAbsenceTool(), UpdateAbsenceTool(), DeleteAbsenceTool(),
    UpdateEmployeeTool(), CancelAgencyTool(), CandidatesTool(), ReportUnderstaffingTool(), WithdrawReportTool(),
    WorkforcePayrollExportTool(), WorkforceSendMessageTool(),
)
from opsdesk.workforce.apps_tools import APP_READ_ONLY, APP_TOOLS  # noqa: E402  (3.1 office apps)

WORKFORCE_TOOLS = WORKFORCE_TOOLS + APP_TOOLS
WORKFORCE_TOOLS_BY_NAME = {t.name: t for t in WORKFORCE_TOOLS}
WORKFORCE_READ_ONLY = frozenset({"requests.list", "roster.candidates"}) | APP_READ_ONLY
