"""Human-readable audit-log lines for accepted changes (shown in /audit, part of the app state)."""

from __future__ import annotations

from datetime import date
from typing import Any

from opsdesk.core.state import WorldState
from opsdesk.workforce.i18n import fmt_date, fmt_short, shift_name
from opsdesk.workforce.state import WorkforceState

ACTIONS = {
    "roster.assign": ("Shift assigned", "Dienst eingeplant"),
    "roster.unassign": ("Removed from shift", "Aus Dienst entfernt"),
    "roster.swap": ("Shifts swapped", "Dienste getauscht"),
    "roster.publish": ("Rota published", "Dienstplan veröffentlicht"),
    "messaging.send": ("Message sent", "Nachricht gesendet"),
    "mail.send": ("Mail sent", "Mail gesendet"),
    "chat.post": ("Chat message posted", "Chatnachricht gesendet"),
    "calendar.create": ("Calendar entry added", "Termin eingetragen"),
    "agency.request_staff": ("Agency cover booked", "Zeitarbeit gebucht"),
    "agency.cancel_request": ("Agency booking cancelled", "Zeitarbeit storniert"),
    "requests.decide": ("Swap request decided", "Tauschwunsch entschieden"),
    "absences.record": ("Absence recorded", "Abwesenheit erfasst"),
    "absences.update": ("Absence changed", "Abwesenheit geändert"),
    "absences.delete": ("Absence deleted", "Abwesenheit gelöscht"),
    "hr.update_employee": ("Staff record changed", "Stammdaten geändert"),
    "reports.understaffing": ("Understaffing reported", "Unterbesetzung gemeldet"),
    "reports.withdraw_understaffing": ("Understaffing report withdrawn", "Meldung zurückgezogen"),
    "works_council.submit": ("Submitted to works council", "Beim Betriebsrat eingereicht"),
    "payroll.export": ("Payroll export created", "Lohnexport erstellt"),
    "timeclock.attempt_edit": ("Time-clock correction requested", "Korrektur Zeiterfassung angefordert"),
}


def _name(state: WorldState, employee_id: str | None) -> str:
    e = next((x for x in state.employees if x.employee_id == employee_id), None)
    return e.name if e else (employee_id or "")


def describe(state: WorldState, app: WorkforceState, tool: str, args: dict[str, Any], result: dict[str, Any]
             ) -> tuple[str, str, str, str]:
    loc = app.locale
    pick = 1 if loc == "de-DE" else 0
    action = ACTIONS.get(tool, (tool, tool))[pick]
    data = result.get("data") or {}

    def shift_label(d: str, ward: str, shift: str) -> str:
        return f"{fmt_short(loc, date.fromisoformat(d))} · {app.unit_names.get(ward, ward)} · {shift_name(loc, shift)}"

    if tool == "roster.assign":
        a = data.get("assignment", {})
        return action, _name(state, a.get("employee_id")), "", shift_label(a["date"], a["ward_id"], a["shift_type"])
    if tool == "roster.unassign":
        return action, str(args.get("assignment_id")), str(args.get("assignment_id")), ""
    if tool in ("roster.swap",):
        return action, f"{args.get('first_assignment_id')} ↔ {args.get('second_assignment_id')}", "", ""
    if tool == "messaging.send":
        return action, _name(state, args.get("recipient_id")), "", str(args.get("subject", ""))[:80]
    if tool == "requests.decide":
        return action, str(args.get("request_id")), "", f"{data.get('status', '')} {data.get('reason', '')}".strip()
    if tool.startswith("absences."):
        absence = data.get("absence") or {}
        who = _name(state, absence.get("employee_id") or args.get("employee_id"))
        span = ""
        if absence:
            span = (f"{fmt_date(loc, date.fromisoformat(absence['start_date']), weekday=False)}–"
                    f"{fmt_date(loc, date.fromisoformat(absence['end_date']), weekday=False)}")
        return action, who or str(args.get("absence_id")), "", span
    if tool == "mail.send":
        return action, ", ".join(args.get("to", [])), "", str(args.get("subject", ""))[:80]
    if tool == "chat.post":
        where = next((c.name for c in app.chat_channels if c.channel_id == data.get("channel_id")), data.get("channel_id", ""))
        return action, where, "", str(args.get("text", ""))[:80]
    if tool == "calendar.create":
        return action, str(args.get("title", "")), "", f"{args.get('start', '')} – {args.get('end', '')}"
    if tool == "hr.update_employee":
        return action, _name(state, args.get("employee_id")), "", ", ".join(data.get("updated", []))
    if tool == "agency.request_staff":
        r = data.get("request") or data
        if isinstance(r, dict) and r.get("requested_for"):
            return action, str(r.get("agency_name") or r.get("agency_id", "")), "", shift_label(
                r["requested_for"], r["ward_id"], r["shift_type"])
        return action, str(args.get("offer_id", "")), "", ""
    if tool == "reports.understaffing":
        return action, app.home.manager_name, "", f"{data.get('shifts', 0)}"
    return action, "", "", ""
