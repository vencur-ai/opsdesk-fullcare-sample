"""3.1 screens of the office apps: Mail, Team chat and Calendar (server-rendered like the rota).

Each view returns (title, area, body, side): `area` picks the app's look and header, `side` its own left
column (folders, channels, mini agenda). Reading a mail goes through `mail.read` (the server calls it), every
form posts one tool call, so the GUI and the tool API stay one state.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from opsdesk.workforce.gui.html import (badge, button, e, form, hidden, page_number, pager, paginate, select, table,
                                        text_input, textarea, url)
from opsdesk.workforce.i18n import WEEKDAYS_LONG, fmt_date, fmt_short, t


def _when(loc: str, stamp: str, today: date) -> str:
    d = date.fromisoformat(stamp[:10])
    return stamp[11:16] if d == today else f"{fmt_short(loc, d)} {stamp[11:16]}"


def _who(ws, sender_id: str) -> str:
    if sender_id == "me":
        return t(ws.app.locale, "chat.me")
    if sender_id == "manager":
        return ws.app.home.manager_name
    return next((x.name for x in ws.state.employees if x.employee_id == sender_id), sender_id)


# ------------------------------------------------------------------------------------------- notifications

def notification_items(ws, seen: str) -> list[dict[str, Any]]:
    """New things for the planner across the apps since `seen` (simulation time): mails, chat posts by others,
    rota inbox notes. Screen-only; the workspace state does not change."""
    app, st = ws.app, ws.state
    out = [{"at": m.sent_at, "app": "mail", "who": m.sender_name, "text": m.subject, "href": f"/mail/{m.mail_id}"}
           for m in app.mails if m.folder == "inbox" and not m.is_read and m.sent_at > seen]
    chans = {c.channel_id: c.name for c in app.chat_channels}
    out += [{"at": m.sent_at, "app": "chat", "who": _who(ws, m.sender_id), "text": f"{chans.get(m.channel_id, '')}: {m.text[:70]}",
             "href": f"/chat?c={m.channel_id}"} for m in app.chat_messages if m.sender_id != "me" and m.sent_at > seen
            and m.sent_at[:10] == st.current_date.isoformat()]
    meta = {m.message_id: m for m in app.message_meta}
    out += [{"at": f"{m.sent_on.isoformat()} 00:00", "app": "rota", "day_only": True, "who": meta[m.message_id].sender_label if m.message_id in meta else "",
             "text": m.subject, "href": f"/messages/{m.message_id}"} for m in st.messages
            if m.recipient_id == "manager" and not m.is_read and f"{m.sent_on.isoformat()} 00:00" > seen
            and getattr(meta.get(m.message_id), "channel", "") != "email"]
    return sorted(out, key=lambda x: x["at"], reverse=True)


def notifications(ws, seen: str) -> tuple[str, str, str, str]:
    loc = ws.app.locale
    items = notification_items(ws, seen)
    def when(i: dict[str, Any]) -> str:  # rota notes carry a date, not a time
        if i.get("day_only"):
            d = date.fromisoformat(i["at"][:10])
            return t(loc, "today") if d == ws.state.current_date else fmt_short(loc, d)
        return _when(loc, i["at"], ws.state.current_date)

    rows = [[e(when(i)), e(t(loc, "notif." + i["app"])), e(i["who"]),
             f'<a href="{e(i["href"])}">{e(i["text"])}</a>'] for i in items[:30]]
    body = (f'<div class="toolbar"><div class="spacer"></div><a class="btn secondary" href="/notifications?seen=1">'
            f'{e(t(loc, "notif.seen"))}</a></div>'
            + (table([t(loc, "date"), t(loc, "channel"), t(loc, "mail.from"), t(loc, "subject")], rows) if rows
               else f'<p class="empty">{e(t(loc, "notif.empty"))}</p>'))
    return t(loc, "notif.title"), "rota", body, None


# ------------------------------------------------------------------------------------------- mail

def _mail_side(ws, folder: str) -> str:
    loc, app = ws.app.locale, ws.app
    unread = sum(1 for m in app.mails if m.folder == "inbox" and not m.is_read)
    items = []
    for key in ("inbox", "sent", "archive"):
        count = f' <span class="count">{unread}</span>' if key == "inbox" and unread else ""
        current = ' aria-current="page" class="active"' if key == folder else ""
        items.append(f'<li><a href="/mail?folder={key}"{current}>{e(t(loc, "mail." + key))}{count}</a></li>')
    return (f'<div class="side-action"><a class="btn" href="/mail/compose">{e(t(loc, "mail.compose"))}</a></div>'
            f'<ul>{"".join(items)}</ul>'
            f'<ul><li><a href="/mail/contacts"{" class=\"active\"" if folder == "contacts" else ""}>'
            f'{e(t(loc, "mail.contacts"))}</a></li></ul>')


def mail_list(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, app, st = ws.app.locale, ws.app, ws.state
    folder = q.get("folder") if q.get("folder") in ("inbox", "sent", "archive") else "inbox"
    query = (q.get("q") or "").strip()
    result = ws.step("mail.list", {"folder": folder, "query": query, "page": page_number(q)})
    data = result.get("data") or {"mails": [], "total": 0, "pages": 1, "page": 1}
    by_id = {m.mail_id: m for m in app.mails}
    rows = []
    for item in data["mails"]:
        m = by_id[item["mail_id"]]
        who = m.sender_name if folder == "inbox" else ", ".join(m.to)
        clip = " 📎" if m.attachments else ""
        link = f'<a href="/mail/{e(m.mail_id)}">{e(m.subject)}</a>{clip}'
        strong = (lambda s: f"<strong>{s}</strong>") if folder == "inbox" and not m.is_read else (lambda s: s)
        preview = e(" ".join(m.body.split())[:90])
        rows.append([strong(e(who)), strong(link) + f'<div class="preview">{preview}</div>', e(_when(loc, m.sent_at, st.current_date))])
    search = form("/mail", hidden(folder=folder) + f'<div class="field">{text_input("q", t(loc, "mail.search"), query, kind="search", extra="size=\"40\"")}'
                  f'<span class="hint">{e(t(loc, "mail.search_hint"))}</span></div>'
                  f'<div class="field">{button(t(loc, "search"), kind="secondary")}</div>', method="get", cls="toolbar")
    empty = t(loc, "mail.none_found") if query else t(loc, "mail.empty")
    body = search + (table([t(loc, "mail.from") if folder == "inbox" else t(loc, "mail.to"), t(loc, "subject"),
                            t(loc, "mail.received") if folder == "inbox" else t(loc, "mail.sent_at")], rows, cls="grid mails")
                     if rows else f'<p class="empty">{e(empty)}</p>') \
        + pager(loc, "/mail", data["page"], data["pages"], data["total"], folder=folder, q=query or None)
    return t(loc, "mail." + folder), "mail", body, _mail_side(ws, folder)


def mail_detail(ws, mail_id: str) -> tuple[str, str, str, str]:
    loc, app = ws.app.locale, ws.app
    result = ws.step("mail.read", {"mail_id": mail_id})
    m = next((x for x in app.mails if x.mail_id == mail_id), None)
    if not result.get("ok") or m is None:
        return t(loc, "app.mail"), "mail", f'<p class="empty">{e(t(loc, "empty.not_found"))}</p>', _mail_side(ws, "inbox")
    names = {c.address: f"{c.name} <{c.address}>" for c in app.contacts}
    names[app.mail_address] = f"{app.home.user_name} <{app.mail_address}>"
    head = [(t(loc, "mail.from"), f"{m.sender_name} <{m.sender}>"), (t(loc, "mail.to"), ", ".join(names.get(a, a) for a in m.to))]
    if m.cc:
        head.append((t(loc, "mail.cc"), ", ".join(names.get(a, a) for a in m.cc)))
    head.append((t(loc, "date"), fmt_date(loc, date.fromisoformat(m.sent_at[:10])) + " " + m.sent_at[11:16]))
    meta = "".join(f'<div><span class="muted">{e(k)}:</span> {e(v)}</div>' for k, v in head)
    files = ""
    if m.attachments:
        files = (f'<h3>{e(t(loc, "mail.attachments"))}</h3><ul class="files">'
                 + "".join(f'<li>📎 {e(a.filename)} · <a href="/mail/attachment/{e(a.attachment_id)}?view=1">{e(t(loc, "mail.view"))}</a>'
                           f' · <a href="/mail/attachment/{e(a.attachment_id)}">{e(t(loc, "mail.download"))}</a></li>' for a in m.attachments)
                 + "</ul>")
    actions = f'<p><a class="btn secondary" href="{e(url("/mail/compose", reply=m.mail_id))}">{e(t(loc, "mail.reply"))}</a></p>'
    body = f'<article class="panel mail"><div class="meta">{meta}</div><h2>{e(m.subject)}</h2><div class="doc">{e(m.body)}</div>{files}</article>{actions}'
    return m.subject, "mail", body, _mail_side(ws, m.folder)


def attachment_view(ws, attachment_id: str) -> tuple[str, str, str, str]:
    loc = ws.app.locale
    result = ws.step("mail.attachment", {"attachment_id": attachment_id})
    if not result.get("ok"):
        return t(loc, "app.mail"), "mail", f'<p class="empty">{e(t(loc, "empty.not_found"))}</p>', _mail_side(ws, "inbox")
    data = result["data"]
    if data["media_type"] == "text/csv":
        sep = ";" if data["text"].split("\n", 1)[0].count(";") > data["text"].split("\n", 1)[0].count(",") else ","
        lines = [line.split(sep) for line in data["text"].strip().splitlines()]
        content = table([e(h) for h in lines[0]], [[e(c) for c in row] for row in lines[1:]]) if lines else ""
    else:
        content = f'<div class="doc">{e(data["text"])}</div>'
    back = f'<p><a href="/mail/{e(data["mail_id"])}">← {e(t(loc, "app.mail"))}</a> · <a href="/mail/attachment/{e(attachment_id)}">{e(t(loc, "mail.download"))}</a></p>'
    return data["filename"], "mail", back + content, _mail_side(ws, "inbox")


def mail_compose(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, app = ws.app.locale, ws.app
    parent = next((m for m in app.mails if m.mail_id == q.get("reply")), None)
    to = q.get("to", "")
    subject, quoted, reply_id = q.get("subject", ""), "", ""
    if parent:
        to = parent.sender if parent.folder == "inbox" else ", ".join(parent.to)
        subject = parent.subject if parent.subject.lower().startswith(("re:", "aw:")) else ("AW: " if loc == "de-DE" else "Re: ") + parent.subject
        quoted = "\n\n" + "\n".join("> " + line for line in parent.body.splitlines())
        reply_id = parent.mail_id
    book = "".join(f'<option value="{e(c.address)}">{e(c.name)} – {e(c.organisation or c.role)}</option>' for c in app.contacts)
    inner = (hidden(in_reply_to=reply_id or None)
             + f'<div class="row"><div>{text_input("to", t(loc, "mail.to"), to, required=True, extra="size=\"70\" list=\"book\"")}'
             f'<span class="hint">{e(t(loc, "mail.addresses_hint"))}</span></div></div>'
             f'<div class="row"><div>{text_input("cc", t(loc, "mail.cc"), "", extra="size=\"70\" list=\"book\"")}</div></div>'
             f'<datalist id="book">{book}</datalist>'
             f'<div class="row"><div>{text_input("subject", t(loc, "subject"), subject, required=True, extra="size=\"70\"")}</div></div>'
             + textarea("body", t(loc, "message"), quoted, required=True, rows=12) + button(t(loc, "send")))
    return t(loc, "mail.compose"), "mail", form("/mail/send", inner), _mail_side(ws, "compose")


def mail_contacts(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc = ws.app.locale
    rows = [[e(c.name), e(c.organisation), e(c.role), f'<a href="{e(url("/mail/compose", to=c.address))}">{e(c.address)}</a>']
            for c in sorted(ws.app.contacts, key=lambda c: (c.organisation, c.name))]
    body = table([t(loc, "name"), "Organisation", t(loc, "mail.contacts.role") if False else ("Funktion" if loc == "de-DE" else "Role"),
                  "E-Mail" if loc == "de-DE" else "Email"], rows)
    return t(loc, "mail.contacts"), "mail", body, _mail_side(ws, "contacts")


# ------------------------------------------------------------------------------------------- team chat

def _chat_side(ws, current: str) -> str:
    loc, app = ws.app.locale, ws.app
    groups = [c for c in app.chat_channels if c.kind == "group"]
    direct = [c for c in app.chat_channels if c.kind == "direct"]

    def item(c) -> str:
        active = ' aria-current="page" class="active"' if c.channel_id == current else ""
        mark = "#" if c.kind == "group" else "@"
        return f'<li><a href="/chat?c={e(c.channel_id)}"{active}><span>{mark} {e(c.name)}</span></a></li>'
    return (f'<h4>{e(t(loc, "chat.channels"))}</h4><ul>{"".join(item(c) for c in groups)}</ul>'
            f'<h4>{e(t(loc, "chat.direct"))}</h4><ul>{"".join(item(c) for c in direct)}</ul>'
            + form("/chat", select("dm", t(loc, "chat.new_direct"), [("", "–")] + sorted(
                ((x.employee_id, x.name) for x in ws.state.employees), key=lambda p: p[1])) + button(t(loc, "chat.write"), kind="secondary"),
                method="get", cls="side-form"))


def chat(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, app, st = ws.app.locale, ws.app, ws.state
    if q.get("dm") and not any(x.employee_id == q["dm"] for x in st.employees):
        q = {k: v for k, v in q.items() if k != "dm"}  # unknown person: show the channels (3.1.1)
    if q.get("dm"):
        existing = next((c for c in app.chat_channels if c.kind == "direct" and set(c.members) == {"me", q["dm"]}), None)
        current, dm_target = (existing.channel_id, None) if existing else ("", q["dm"])
    else:
        current, dm_target = q.get("c") or (app.chat_channels[0].channel_id if app.chat_channels else ""), None
    channel = next((c for c in app.chat_channels if c.channel_id == current), None)
    if channel is None and dm_target is None:
        return t(loc, "app.chat"), "chat", f'<p class="empty">{e(t(loc, "empty.not_found"))}</p>', _chat_side(ws, "")
    page_no = page_number(q)
    stream, title, info = "", "", ""
    if channel is not None:
        data = ws.step("chat.read", {"channel_id": channel.channel_id, "page": page_no}).get("data") or {"messages": [], "total": 0}
        last_day = None
        for m in data["messages"]:
            day = m["sent_at"][:10]
            if day != last_day:
                stream += f'<div class="day">{e(fmt_date(loc, date.fromisoformat(day)))}</div>'
                last_day = day
            mine = " mine" if m["sender_id"] == "me" else ""
            about = ""
            full = next((x for x in app.chat_messages if x.chat_id == m["chat_id"]), None)
            if full is not None and full.shift_date and full.shift_type and full.ward_id:
                label = (f'{fmt_short(loc, full.shift_date)} · {app.unit_names.get(full.ward_id, full.ward_id)} · '
                         f'{t(loc, "shift." + full.shift_type.value)}')
                about = f'<div class="about">{e(t(loc, "chat.ask_label" if full.ask else "chat.about_label"))}: {e(label)}</div>'
            stream += (f'<div class="post{mine}"><div class="by"><strong>{e(_who(ws, m["sender_id"]))}</strong> '
                       f'<span class="muted">{e(m["sent_at"][11:16])}</span></div>{about}<div class="text">{e(m["text"])}</div></div>')
        older = (f'<a href="{e(url("/chat", c=channel.channel_id, page=page_no + 1))}">{e(t(loc, "chat.older"))}</a>'
                 if data["total"] > page_no * 25 else "")
        newer = (f' <a href="{e(url("/chat", c=channel.channel_id, page=page_no - 1))}">{e(t(loc, "chat.newer"))}</a>' if page_no > 1 else "")
        title = ("# " if channel.kind == "group" else "@ ") + channel.name
        if channel.kind == "direct":  # a direct message shows who the colleague is, not "2 members"
            from opsdesk.workforce.gui.pages import _role
            other = next((x for x in st.employees if x.employee_id in channel.members), None)
            about = _role(loc, {q.value for q in other.qualifications}) if other else ""
        else:
            about = f'{channel.topic}{" · " if channel.topic else ""}{len(channel.members)} {t(loc, "chat.members")}'
        info = (f'<p class="muted">{e(about)}</p>' + (f'<p class="pager">{older}{newer}</p>' if older or newer else ""))
        target = hidden(channel_id=channel.channel_id)
    else:
        name = next(x.name for x in st.employees if x.employee_id == dm_target)
        title, target = "@ " + name, hidden(employee_id=dm_target)
    from opsdesk.workforce.gui.html import checkbox
    days = [(d.isoformat(), fmt_short(loc, d)) for d in (st.start_date + timedelta(days=i) for i in range(st.horizon_days))]
    about = (f'<details class="about-shift"><summary>{e(t(loc, "chat.about_shift"))}</summary><div class="row"><div>'
             + select("shift_date", t(loc, "date"), [("", "–")] + days, "", ident="c-date") + "</div><div>"
             + select("ward_id", t(loc, "unit"), [("", "–")] + [(w, n) for w, n in sorted(app.unit_names.items())], "", ident="c-unit")
             + "</div><div>" + select("shift_type", t(loc, "shift"), [("", "–")] + [(s, t(loc, "shift." + s)) for s in ("early", "late", "night")],
                                       "", ident="c-shift")
             + "</div></div>" + checkbox("ask", t(loc, "chat.ask"), False, ident="c-ask") + "</details>")
    composer = form("/chat/post", target + textarea("text", t(loc, "chat.write"), "", required=True, rows=3) + about + button(t(loc, "send")),
                    cls="form composer")
    body = info + f'<div class="stream">{stream or "<p class=\"empty\">" + e(t(loc, "chat.empty")) + "</p>"}</div>' + composer
    return title, "chat", body, _chat_side(ws, current)


# ------------------------------------------------------------------------------------------- calendar

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _cal_side(ws, first: date) -> str:
    loc, app, st = ws.app.locale, ws.app, ws.state
    soon = [ev for ev in sorted(app.calendar, key=lambda x: x.start) if ev.end[:10] >= st.current_date.isoformat()][:6]
    items = "".join(f'<li><a href="/calendar/{e(ev.event_id)}"><span>{e(fmt_short(loc, date.fromisoformat(ev.start[:10])))} · '
                    f'{e(ev.title)}</span></a></li>' for ev in soon)
    return (f'<div class="side-action"><a class="btn" href="/calendar/new">{e(t(loc, "cal.new"))}</a></div>'
            f'<h4>{e(t(loc, "cal.upcoming"))}</h4><ul>{items}</ul>')


def calendar_week(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, app, st = ws.app.locale, ws.app, ws.state
    try:
        first = _monday(date.fromisoformat(q["start"])) if q.get("start") else _monday(st.current_date)
    except ValueError:
        first = _monday(st.current_date)
    days = [first + timedelta(days=i) for i in range(7)]
    kind = q.get("kind") or ""
    events = ws.step("calendar.list", {"start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
                                       "kind": kind or "all"}).get("data") or []
    cols = []
    for d in days:
        cells = []
        for ev in events:
            if not ev["start"][:10] <= d.isoformat() <= ev["end"][:10]:
                continue
            when = t(loc, "cal.all_day") if ev["all_day"] else f'{ev["start"][11:16]}–{ev["end"][11:16]}'
            cells.append(f'<a class="ev ev-{e(ev["kind"])}" href="/calendar/{e(ev["event_id"])}"><span class="when">{e(when)}</span> '
                         f'{e(ev["title"])}</a>')
        today = " today" if d == st.current_date else ""
        cols.append(f'<div class="day{today}"><div class="dh">{e(WEEKDAYS_LONG[loc][d.weekday()])}<br>'
                    f'<span class="muted">{e(fmt_short(loc, d))}</span></div>{"".join(cells)}</div>')
    nav = (f'<div class="toolbar"><a class="btn secondary" href="{e(url("/calendar", start=(first - timedelta(days=7)).isoformat(), kind=kind))}">← {e(t(loc, "cal.previous"))}</a>'
           f'<a class="btn secondary" href="{e(url("/calendar", kind=kind))}">{e(t(loc, "cal.today"))}</a>'
           f'<a class="btn secondary" href="{e(url("/calendar", start=(first + timedelta(days=7)).isoformat(), kind=kind))}">{e(t(loc, "cal.next"))} →</a>'
           '<div class="spacer"></div>'
           + form("/calendar", hidden(start=first.isoformat()) + select("kind", t(loc, "cal.kind"), [("", t(loc, "all"))] + [
               (k, t(loc, "cal.kind." + k)) for k in ("training", "council", "inspection", "leave", "meeting", "other")], kind)
               + " " + button(t(loc, "filter"), kind="secondary"), method="get", cls="inline") + "</div>")
    title = f'{t(loc, "cal.week")} {first.isocalendar().week} · {fmt_short(loc, days[0])} – {fmt_short(loc, days[-1])}'
    body = nav + f'<div class="week">{"".join(cols)}</div>' + ("" if events else f'<p class="empty">{e(t(loc, "cal.none"))}</p>')
    return title, "calendar", body, _cal_side(ws, first)


def calendar_event(ws, event_id: str) -> tuple[str, str, str, str]:
    loc, app, st = ws.app.locale, ws.app, ws.state
    ev = next((x for x in app.calendar if x.event_id == event_id), None)
    if ev is None:
        return t(loc, "app.calendar"), "calendar", f'<p class="empty">{e(t(loc, "empty.not_found"))}</p>', _cal_side(ws, st.current_date)
    names = {x.employee_id: x.name for x in st.employees} | {c.contact_id: c.name for c in app.contacts}
    start_d, end_d = date.fromisoformat(ev.start[:10]), date.fromisoformat(ev.end[:10])
    when = fmt_date(loc, start_d) + (f" – {fmt_date(loc, end_d)}" if end_d != start_d else "")
    when += f" · {t(loc, 'cal.all_day')}" if ev.all_day else f" · {ev.start[11:16]}–{ev.end[11:16]}"
    facts = [(t(loc, "cal.kind"), t(loc, "cal.kind." + ev.kind)), (t(loc, "date"), when)]
    if ev.location:
        facts.append((t(loc, "cal.location"), ev.location))
    if ev.attendees:
        facts.append((t(loc, "cal.attendees"), ", ".join(names.get(a, a) for a in ev.attendees)))
    if ev.notes:
        facts.append((t(loc, "cal.notes"), ev.notes))
    if ev.created_by:
        facts.append((t(loc, "cal.created_by"), ev.created_by))
    from opsdesk.workforce.gui.html import kv_table
    body = (f'<div class="panel">{kv_table((e(k), e(v)) for k, v in facts)}</div>'
            f'<p><a href="{e(url("/calendar", start=start_d.isoformat()))}">← {e(t(loc, "app.calendar"))}</a></p>')
    return ev.title, "calendar", body, _cal_side(ws, start_d)


def calendar_new(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, app, st = ws.app.locale, ws.app, ws.state
    day = q.get("date") or st.current_date.isoformat()
    people = sorted(((x.employee_id, x.name) for x in st.employees), key=lambda p: p[1])
    attendees = "".join(f'<label class="pick"><input type="checkbox" id="att-{e(v)}" name="attendees" value="{e(v)}"> {e(n)}</label>'
                        for v, n in people)  # 3.1.1: a checkbox list instead of a modifier-click multi-select
    inner = (f'<div class="row"><div>{text_input("title", t(loc, "cal.title"), "", required=True, extra="size=\"50\"")}</div>'
             f'<div>{select("kind", t(loc, "cal.kind"), [(k, t(loc, "cal.kind." + k)) for k in ("meeting", "training", "council", "inspection", "leave", "other")], "meeting")}</div></div>'
             f'<div class="row"><div>{text_input("start_date", t(loc, "cal.start"), day, kind="date", required=True)}</div>'
             f'<div>{text_input("start_time", t(loc, "cal.time"), "10:00", kind="time", required=True)}</div>'
             f'<div>{text_input("end_date", t(loc, "cal.end"), day, kind="date", required=True)}</div>'
             f'<div>{text_input("end_time", t(loc, "cal.time"), "11:00", kind="time", required=True)}</div>'
             f'<div>{text_input("location", t(loc, "cal.location"), "")}</div></div>'
             f'<fieldset class="picklist"><legend>{e(t(loc, "cal.attendees"))}</legend>{attendees}</fieldset>'
             + textarea("notes", t(loc, "cal.notes"), "", rows=3) + button(t(loc, "save")))
    return t(loc, "cal.new"), "calendar", form("/calendar/new", inner), _cal_side(ws, st.current_date)
