"""3.1 tools of the three office apps next to the rota: Mail, Team chat and Calendar.

They work on the same workspace as the rota (`WorkforceState`), on the same frozen clock, and every
call is one `AppTool` like the rota's, so GUI and API stay one state. Outgoing mails and chat posts are
records of the workspace (part of the state hash and of what the verifier reads); nothing is sent
anywhere.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import Field, field_validator

from opsdesk.apps.base import AppTool, ToolArgs, ToolError
from opsdesk.apps.ids import next_monotonic_id
from opsdesk.core.models import ShiftType
from opsdesk.core.state import WorldState
from opsdesk.workforce.state import CalendarEvent, ChatChannel, ChatMessage, MailMessage

PAGE = 25
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


def _app():
    from opsdesk.workforce.tools import _app as current
    return current()


def _summary(m: MailMessage) -> dict[str, Any]:
    return {"mail_id": m.mail_id, "folder": m.folder, "from": f"{m.sender_name} <{m.sender}>", "to": m.to,
            "subject": m.subject, "sent_at": m.sent_at, "is_read": m.is_read, "thread_id": m.thread_id,
            "attachments": [a.filename for a in m.attachments]}


def _who(app, sender_id: str) -> str:
    if sender_id == "me":
        return app.home.user_name
    if sender_id == "manager":
        return app.home.manager_name
    return sender_id


# ---------------------------------------------------------------- mail

class ListMailArgs(ToolArgs):
    folder: Literal["inbox", "sent", "archive", "all"] = "inbox"
    query: str = Field(default="", max_length=200, description="Words that must appear in sender, subject or text.")
    unread_only: bool = False
    page: int = Field(default=1, ge=1)


class ListMailTool(AppTool):
    app = "mail"
    name = "mail.list"
    description = ("List mails in a folder of the office mailbox, newest first (25 per page): sender, recipients, "
                   "subject, time, read flag and attachment names. `query` matches whole words in sender, subject and text.")
    args_model = ListMailArgs

    def execute(self, state: WorldState, args: ListMailArgs) -> dict[str, Any]:
        words = [w.casefold() for w in args.query.split()]
        items = []
        for m in sorted(_app().mails, key=lambda m: (m.sent_at, m.mail_id), reverse=True):
            if args.folder != "all" and m.folder != args.folder:
                continue
            if args.unread_only and m.is_read:
                continue
            haystack = set(re.findall(r"\w+", f"{m.sender_name} {m.sender} {m.subject} {m.body}".casefold()))
            if words and not all(w in haystack for w in words):
                continue
            items.append(m)
        start = (args.page - 1) * PAGE
        return {"total": len(items), "page": args.page, "pages": max(1, -(-len(items) // PAGE)),
                "mails": [_summary(m) for m in items[start:start + PAGE]]}


class ReadMailArgs(ToolArgs):
    mail_id: str = Field(min_length=1)


class ReadMailTool(AppTool):
    app = "mail"
    name = "mail.read"
    description = "Open one mail (full text, recipients, attachments) and mark it as read."
    args_model = ReadMailArgs

    def execute(self, state: WorldState, args: ReadMailArgs) -> dict[str, Any]:
        m = next((x for x in _app().mails if x.mail_id == args.mail_id), None)
        if m is None:
            raise ToolError("Mail not found.")
        m.is_read = True
        return {**_summary(m), "cc": m.cc, "body": m.body, "in_reply_to": m.in_reply_to,
                "attachments": [{"attachment_id": a.attachment_id, "filename": a.filename, "media_type": a.media_type}
                                for a in m.attachments]}


class AttachmentArgs(ToolArgs):
    attachment_id: str = Field(min_length=1)


class AttachmentTool(AppTool):
    app = "mail"
    name = "mail.attachment"
    description = "Read the text of one mail attachment (CSV as text, PDF as its extracted text)."
    args_model = AttachmentArgs

    def execute(self, state: WorldState, args: AttachmentArgs) -> dict[str, Any]:
        for m in _app().mails:
            for a in m.attachments:
                if a.attachment_id == args.attachment_id:
                    return {"attachment_id": a.attachment_id, "filename": a.filename, "media_type": a.media_type,
                            "mail_id": m.mail_id, "text": a.content}
        raise ToolError("Attachment not found.")


class SendMailArgs(ToolArgs):
    to: list[str] = Field(min_length=1, max_length=20)
    cc: list[str] = Field(default_factory=list, max_length=20)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    in_reply_to: str | None = None

    @field_validator("to", "cc")
    @classmethod
    def addresses(cls, value: list[str]) -> list[str]:
        out = [v.strip() for v in value if v.strip()]
        bad = [v for v in out if not ADDRESS.match(v)]
        if bad:
            raise ValueError(f"not a mail address: {', '.join(bad)}")
        return out


class SendMailTool(AppTool):
    app = "mail"
    name = "mail.send"
    description = ("Send a mail from the office mailbox (also used to reply: give `in_reply_to`). Recipients are mail "
                   "addresses from the address book; the mail is kept in Sent.")
    args_model = SendMailArgs

    def execute(self, state: WorldState, args: SendMailArgs) -> dict[str, Any]:
        app = _app()
        known = {c.address.casefold() for c in app.contacts} | {app.mail_address.casefold()}
        unknown = [a for a in args.to + args.cc if a.casefold() not in known]
        if unknown:
            raise ToolError(f"Not in the address book: {', '.join(unknown)}.")
        parent = None
        if args.in_reply_to:
            parent = next((m for m in app.mails if m.mail_id == args.in_reply_to), None)
            if parent is None:
                raise ToolError("The mail to reply to was not found.")
        mail = MailMessage(mail_id=next_monotonic_id("mail", [m.mail_id for m in app.mails]), folder="sent",
                           sender=app.mail_address, sender_name=app.home.user_name, to=args.to, cc=args.cc,
                           subject=args.subject.strip(), body=args.body.strip(), sent_at=app.now(state.current_date),
                           is_read=True, thread_id=parent.thread_id if parent else f"thread-{len(app.mails) + 1:04d}",
                           in_reply_to=parent.mail_id if parent else None)
        app.mails.append(mail)
        return {"mail_id": mail.mail_id, "folder": "sent", "sent_at": mail.sent_at, "thread_id": mail.thread_id}


class ContactsArgs(ToolArgs):
    query: str = Field(default="", max_length=100)


class ContactsTool(AppTool):
    app = "mail"
    name = "mail.contacts"
    description = "Search the mail address book (name, organisation, role, address)."
    args_model = ContactsArgs

    def execute(self, state: WorldState, args: ContactsArgs) -> list[dict[str, Any]]:
        q = args.query.casefold().strip()
        return [c.model_dump(mode="json") for c in _app().contacts
                if not q or q in f"{c.name} {c.organisation} {c.role} {c.address}".casefold()]


# ---------------------------------------------------------------- team chat

class ChannelsArgs(ToolArgs):
    pass


class ChannelsTool(AppTool):
    app = "chat"
    name = "chat.channels"
    description = "List the team chat's group channels and direct conversations with their newest message time."
    args_model = ChannelsArgs

    def execute(self, state: WorldState, args: ChannelsArgs) -> list[dict[str, Any]]:
        app = _app()
        out = []
        for ch in app.chat_channels:
            posts = [m for m in app.chat_messages if m.channel_id == ch.channel_id]
            out.append({"channel_id": ch.channel_id, "name": ch.name, "kind": ch.kind, "members": ch.members,
                        "topic": ch.topic, "messages": len(posts), "last": max((m.sent_at for m in posts), default=None)})
        return out


class ReadChannelArgs(ToolArgs):
    channel_id: str = Field(min_length=1)
    page: int = Field(default=1, ge=1, description="1 = the newest 25 messages.")


class ReadChannelTool(AppTool):
    app = "chat"
    name = "chat.read"
    description = "Read a chat channel or direct conversation, newest 25 messages per page (page 2 = older)."
    args_model = ReadChannelArgs

    def execute(self, state: WorldState, args: ReadChannelArgs) -> dict[str, Any]:
        app = _app()
        if not any(c.channel_id == args.channel_id for c in app.chat_channels):
            raise ToolError("Channel not found.")
        posts = sorted((m for m in app.chat_messages if m.channel_id == args.channel_id),
                       key=lambda m: (m.sent_at, m.chat_id), reverse=True)
        start = (args.page - 1) * PAGE
        names = {e.employee_id: e.name for e in state.employees}
        return {"channel_id": args.channel_id, "total": len(posts), "page": args.page,
                "messages": [{"chat_id": m.chat_id, "from": names.get(m.sender_id, _who(app, m.sender_id)),
                              "sender_id": m.sender_id, "text": m.text, "sent_at": m.sent_at, "reply_to": m.reply_to}
                             for m in reversed(posts[start:start + PAGE])]}


class PostArgs(ToolArgs):
    channel_id: str | None = Field(default=None, description="A group or direct channel.")
    employee_id: str | None = Field(default=None, description="Write to this person directly (opens the conversation).")
    text: str = Field(min_length=1, max_length=2000)
    reply_to: str | None = None
    shift_date: date | None = Field(default=None, description="Optional: the shift the message is about.")
    shift_type: ShiftType | None = None
    ward_id: str | None = None
    ask: bool = Field(default=False, description="Ask the person (or the channel) to work that shift.")


class PostTool(AppTool):
    app = "chat"
    name = "chat.post"
    description = ("Post a message in a team chat channel, or directly to one employee (`employee_id`). Messages are "
                   "seen by the channel's members, who may answer. Give a shift (date, unit, shift) and `ask` to ask "
                   "whether someone can work it; a reply is not a booking – assign the shift in the rota. A direct "
                   "message with a shift and no `ask` is a shift notice to that person.")
    args_model = PostArgs

    def execute(self, state: WorldState, args: PostArgs) -> dict[str, Any]:
        app = _app()
        if bool(args.channel_id) == bool(args.employee_id):
            raise ToolError("Give either a channel or an employee.")
        if args.employee_id:
            if not any(e.employee_id == args.employee_id for e in state.employees):
                raise ToolError("Employee not found.")
            channel = next((c for c in app.chat_channels if c.kind == "direct" and set(c.members) == {"me", args.employee_id}),
                           None)
            if channel is None:
                name = next(e.name for e in state.employees if e.employee_id == args.employee_id)
                channel = ChatChannel(channel_id=f"dm-{args.employee_id}", name=name, kind="direct",
                                      members=["me", args.employee_id])
                app.chat_channels.append(channel)
        else:
            channel = next((c for c in app.chat_channels if c.channel_id == args.channel_id), None)
            if channel is None:
                raise ToolError("Channel not found.")
        if args.reply_to and not any(m.chat_id == args.reply_to and m.channel_id == channel.channel_id
                                     for m in app.chat_messages):
            raise ToolError("The message to reply to is not in this channel.")
        if args.ask and not (args.shift_date and args.shift_type and args.ward_id):
            raise ToolError("To ask someone to work a shift, give its date, unit and shift.")
        if args.ward_id and not any(w.ward_id == args.ward_id for w in state.wards):
            raise ToolError("Unit not found.")
        text = args.text.strip()
        if channel.kind == "direct" and args.shift_date and args.shift_type and args.ward_id and not args.ask:
            # a direct message about a shift is a shift notice: the rota adds whether the person is on it now (3.1.1)
            from opsdesk.workforce.i18n import fmt_short, shift_name
            who = next(m for m in channel.members if m != "me")
            on = any(a.employee_id == who and a.date == args.shift_date and a.ward_id == args.ward_id
                     and a.shift_type == args.shift_type for a in state.roster.assignments)
            what = f"{fmt_short(app.locale, args.shift_date)} · {app.unit_names.get(args.ward_id, args.ward_id)} · " \
                   f"{shift_name(app.locale, args.shift_type.value)}"
            text += ("\n— Dienstplan: " + what + (" – du bist eingetragen." if on else " – du bist nicht mehr eingetragen.")
                     if app.locale == "de-DE" else
                     "\n— Rota: " + what + (" – you are on this shift." if on else " – you are no longer on this shift."))
        post = ChatMessage(chat_id=next_monotonic_id("chat", [m.chat_id for m in app.chat_messages]),
                           channel_id=channel.channel_id, sender_id="me", text=text,
                           sent_at=app.now(state.current_date), reply_to=args.reply_to, shift_date=args.shift_date,
                           shift_type=args.shift_type, ward_id=args.ward_id, ask=args.ask)
        app.chat_messages.append(post)
        return {"chat_id": post.chat_id, "channel_id": channel.channel_id, "sent_at": post.sent_at}


# ---------------------------------------------------------------- calendar

class ListEventsArgs(ToolArgs):
    start_date: date | None = None
    end_date: date | None = None
    kind: Literal["training", "council", "inspection", "leave", "meeting", "other", "all"] = "all"


class ListEventsTool(AppTool):
    app = "calendar"
    name = "calendar.list"
    description = "List calendar entries (training days, council meetings, inspections, leave, meetings) in a date range."
    args_model = ListEventsArgs

    def execute(self, state: WorldState, args: ListEventsArgs) -> list[dict[str, Any]]:
        out = []
        for ev in sorted(_app().calendar, key=lambda e: (e.start, e.event_id)):
            first, last = date.fromisoformat(ev.start[:10]), date.fromisoformat(ev.end[:10])
            if args.start_date and last < args.start_date or args.end_date and first > args.end_date:
                continue
            if args.kind != "all" and ev.kind != args.kind:
                continue
            out.append(ev.model_dump(mode="json"))
        return out


class CreateEventArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["training", "council", "inspection", "leave", "meeting", "other"] = "meeting"
    start: str = Field(description="YYYY-MM-DD HH:MM")
    end: str = Field(description="YYYY-MM-DD HH:MM")
    location: str = Field(default="", max_length=200)
    attendees: list[str] = Field(default_factory=list, max_length=60)
    notes: str = Field(default="", max_length=2000)

    @field_validator("start", "end")
    @classmethod
    def when(cls, value: str) -> str:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("use YYYY-MM-DD HH:MM") from exc


class CreateEventTool(AppTool):
    app = "calendar"
    name = "calendar.create"
    description = "Add an entry to the home's calendar (for example a meeting or a training session)."
    args_model = CreateEventArgs

    def execute(self, state: WorldState, args: CreateEventArgs) -> dict[str, Any]:
        app = _app()
        if args.end <= args.start:
            raise ToolError("The end must be after the start.")
        first = date.fromisoformat(args.start[:10])
        if not state.start_date - timedelta(days=60) <= first <= state.start_date + timedelta(days=120):
            raise ToolError("Entries can be added from 60 days back to 120 days ahead.")
        known = {e.employee_id for e in state.employees} | {c.contact_id for c in app.contacts}
        unknown = [a for a in args.attendees if a not in known]
        if unknown:
            raise ToolError(f"Unknown attendees: {', '.join(unknown)}.")
        ev = CalendarEvent(event_id=next_monotonic_id("event", [e.event_id for e in app.calendar]), title=args.title.strip(),
                           kind=args.kind, start=args.start, end=args.end, location=args.location.strip(),
                           attendees=list(dict.fromkeys(args.attendees)), notes=args.notes.strip(),
                           created_by=app.home.user_name)
        app.calendar.append(ev)
        return ev.model_dump(mode="json")


APP_TOOLS: tuple[AppTool, ...] = (
    ListMailTool(), ReadMailTool(), AttachmentTool(), SendMailTool(), ContactsTool(),
    ChannelsTool(), ReadChannelTool(), PostTool(), ListEventsTool(), CreateEventTool(),
)
APP_READ_ONLY = frozenset({"mail.list", "mail.read", "mail.attachment", "mail.contacts", "chat.channels", "chat.read",
                           "calendar.list"})
