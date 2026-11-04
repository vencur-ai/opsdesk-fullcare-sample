"""App-side records of OpsDesk Workforce that the 2.4 world model does not hold.

Loaded from the task bundle's `app.json` (everything in it is visible in the app) and changed only
by workforce tools. Part of the state hash, so GUI and API runs can be compared byte for byte.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, PrivateAttr

from opsdesk.core.models import DomainModel, ShiftType

APP_SCHEMA = "workforce-app/v2"  # v2 (3.1): mail, team chat, calendar; v1 files (3.0 tasks) still load
APP_FILE = "app.json"


class HomeInfo(DomainModel):
    address: str
    manager_name: str
    manager_title: str
    user_name: str
    user_title: str
    night_lead_name: str


class EmployeeProfile(DomainModel):
    employee_id: str
    staff_number: str
    phone: str
    hire_date: date
    qualification_valid_until: dict[str, date] = Field(default_factory=dict)
    wishes: list[str] = Field(default_factory=list)
    notes: str = ""
    hours_balance_minutes: int = 0
    previous_period_minutes: int = 0


class AbsenceMeta(DomainModel):
    absence_id: str
    entered_by: str
    entered_on: date
    channel: Literal["phone", "app", "office", "planned"]
    note: str = ""


class MessageMeta(DomainModel):
    message_id: str
    channel: Literal["phone", "app", "email", "note"]
    sender_label: str


class SwapRequest(DomainModel):
    request_id: str
    message_id: str
    requester_id: str
    partner_id: str
    first_assignment_id: str
    second_assignment_id: str
    status: Literal["open", "approved", "declined", "withdrawn"] = "open"
    reason: str = ""
    decided_on: date | None = None
    # 3.1: the partner still has to confirm the swap in the staff app (they do it when the planner asks them)
    partner_confirmed: bool = True


class UnderstaffingItem(DomainModel):
    date: date
    unit: str = Field(min_length=1)
    shift: ShiftType
    missing: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class UnderstaffingReport(DomainModel):
    report_id: str
    sent_on: date
    items: list[UnderstaffingItem]
    note: str = ""
    covered_positions: int
    total_cost_cents: int
    agency_spend_cents: int
    roster_fingerprint: str
    withdrawn: bool = False


class Publication(DomainModel):
    published_on: date
    roster_fingerprint: str
    open_positions: int
    covered_positions: int
    total_cost_cents: int


class AuditEntry(DomainModel):
    at: str  # "YYYY-MM-DD HH:MM", simulation time
    user: str
    action: str
    object: str
    before: str = ""
    after: str = ""


# ------------------------------------------------------------------ 3.1: mail, team chat, calendar

class Contact(DomainModel):
    """An address-book entry of the mail app (people and organisations outside the rota)."""
    contact_id: str
    name: str
    address: str
    organisation: str = ""
    role: str = ""


class MailAttachment(DomainModel):
    attachment_id: str
    filename: str
    media_type: Literal["text/csv", "application/pdf", "text/plain"]
    content: str  # the text of the file (a PDF is rendered from it when downloaded)


class MailMessage(DomainModel):
    mail_id: str
    folder: Literal["inbox", "sent", "archive"]
    sender: str  # address
    sender_name: str
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    sent_at: str  # "YYYY-MM-DD HH:MM", simulation time
    is_read: bool = False
    thread_id: str
    in_reply_to: str | None = None
    attachments: list[MailAttachment] = Field(default_factory=list)


class ChatChannel(DomainModel):
    channel_id: str
    name: str
    kind: Literal["group", "direct"]
    members: list[str]  # employee ids; "me" is the signed-in planner, "manager" the home manager
    topic: str = ""


class ChatMessage(DomainModel):
    chat_id: str
    channel_id: str
    sender_id: str
    text: str
    sent_at: str  # "YYYY-MM-DD HH:MM"
    reply_to: str | None = None
    # optional shift reference ("about this shift"); ask = asking the person to work it
    shift_date: date | None = None
    shift_type: ShiftType | None = None
    ward_id: str | None = None
    ask: bool = False


class CalendarEvent(DomainModel):
    event_id: str
    title: str
    kind: Literal["training", "council", "inspection", "leave", "meeting", "other"]
    start: str  # "YYYY-MM-DD HH:MM"
    end: str
    all_day: bool = False
    location: str = ""
    attendees: list[str] = Field(default_factory=list)  # employee ids or contact ids
    notes: str = ""
    created_by: str = ""


class StagedEvent(DomainModel):
    """3.1: something that happens during the work, not at the start (hidden until its trigger fires).

    `sick_call`: a colleague rings in sick (payload: employee_id, start_date, end_date) once the planner has filled
    the first open position. `withdraw_request`: a requester takes back a swap request (payload: request_id) after
    the planner's first change, if it is still open."""
    staged_id: str
    kind: Literal["sick_call", "withdraw_request"]
    trigger: Literal["gap_filled", "changes"]
    after_changes: int = 1
    payload: dict[str, str] = Field(default_factory=dict)
    fired_at: str | None = None


class WorkforceState(DomainModel):
    schema_version: Literal["workforce-app/v1", "workforce-app/v2"] = APP_SCHEMA
    locale: Literal["en-GB", "de-DE"]
    home: HomeInfo
    unit_names: dict[str, str]
    profiles: list[EmployeeProfile]
    absence_meta: list[AbsenceMeta] = Field(default_factory=list)
    message_meta: list[MessageMeta] = Field(default_factory=list)
    message_texts: dict[str, dict[str, str]] = Field(default_factory=dict)
    requests: list[SwapRequest] = Field(default_factory=list)
    reports: list[UnderstaffingReport] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    history: list[AuditEntry] = Field(default_factory=list)
    session_log: list[AuditEntry] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    mail_address: str = ""  # the signed-in planner's address
    mails: list[MailMessage] = Field(default_factory=list)
    chat_channels: list[ChatChannel] = Field(default_factory=list)
    chat_messages: list[ChatMessage] = Field(default_factory=list)
    calendar: list[CalendarEvent] = Field(default_factory=list)
    staged: list[StagedEvent] = Field(default_factory=list)
    open_at_start: int = 0  # open positions when the workspace was opened (for `gap_filled`)
    budget_in_mail: bool = False  # 3.1 inbox variant: the agency page does not show the budget (it is in a mail)
    _clock_base: int = PrivateAttr(default=8 * 60 + 5)  # minute of the day at the first change; FullCare's early shift sets it

    def profile(self, employee_id: str) -> EmployeeProfile | None:
        return next((p for p in self.profiles if p.employee_id == employee_id), None)

    def now(self, current_date: date) -> str:
        """Simulation time of the next action: 08:05 on the frozen day, two minutes per change; after 300 changes
        (18:05) a minute per three changes, and never past 23:59 (3.1.1: the clock no longer wraps to 00:xx)."""
        n = len(self.session_log)
        minute = min(self._clock_base + 2 * min(n, 300) + max(0, n - 300) // 3, 23 * 60 + 59)
        return f"{current_date.isoformat()} {minute // 60:02d}:{minute % 60:02d}"

    def active_report(self) -> UnderstaffingReport | None:
        live = [r for r in self.reports if not r.withdrawn]
        return live[-1] if live else None
