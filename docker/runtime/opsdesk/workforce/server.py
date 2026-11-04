"""OpsDesk Workforce server: one workspace, GUI + tool API on the same `WorkforceSession`.

    OPSDESK_TASKS_ROOT=<tasks dir> OPSDESK_TASK=<task folder> OPSDESK_HOST_KEY=<key> \
        python -m opsdesk.workforce.server --port 8080

GUI pages at `/`, tool API at `/api/tools` and `/api/step`, host-only routes under `/host/` (host key
in `x-opsdesk-host-key`): reset, finish (claims → score), state hash, export. No route reads the
solver reference except `/host/finish`, which runs the deterministic verifier.
"""

from __future__ import annotations

import argparse
import hmac
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from opsdesk import __version__
from opsdesk.workforce.gui import office_pages, pages
from opsdesk.workforce.gui.html import APPS, page, url
from opsdesk.workforce.i18n import fmt_money, fmt_short, rule_text, shift_name, t
from opsdesk.workforce.session import CALL_SOURCE, WorkforceSession

STATIC = Path(__file__).parent / "gui" / "static"

ERRORS_DE = {
    "Employee already has an overlapping roster assignment.": "Die Person hat zu dieser Zeit schon einen Dienst.",
    "Roster is already published.": "Der Dienstplan ist bereits veröffentlicht.",
    "Works-council approval is required before publication.": "Vor der Veröffentlichung ist die Zustimmung des Betriebsrats nötig.",
    "The roster changed after works-council approval; resubmit it.": "Der Plan wurde nach der Zustimmung geändert; bitte erneut einreichen.",
    "Assignment not found.": "Dienst nicht gefunden.",
    "Employee not found.": "Mitarbeiter/in nicht gefunden.",
    "Request not found.": "Anfrage nicht gefunden.",
    "This request has already been decided.": "Diese Anfrage ist bereits entschieden.",
    "This request was withdrawn by the employee.": "Diese Anfrage wurde von der Person zurückgezogen.",
    "Please give the reason in a few words (the person reads it).": "Bitte die Begründung in ein paar Worten angeben (die Person liest sie).",
    "The swap partner has not confirmed this swap yet.": "Die Tauschpartnerin / der Tauschpartner hat den Tausch noch nicht bestätigt.",
    "Shift date is outside the planning period.": "Das Datum liegt außerhalb des Planungszeitraums.",
    "Each shift can be listed only once.": "Jeder Dienst darf nur einmal aufgeführt werden.",
}


def _form(raw: bytes) -> dict[str, list[str]]:
    return parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)


def _one(f: dict[str, list[str]], key: str, default: str = "") -> str:
    return (f.get(key) or [default])[0]


def create_workforce_app(tasks_root: str | Path, task_id: str | None = None, *,
                         session_factory=WorkforceSession, apps: tuple[tuple[str, str, str], ...] = APPS,
                         title: str = "OpsDesk Workforce") -> FastAPI:
    """`session_factory` and `apps` let a replica built on this workspace (OpsDesk FullCare) bring its own session
    class and its own entries in the app switcher; Workforce itself uses the defaults."""
    app = FastAPI(title=title, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    ws = session_factory(tasks_root)
    app.state.ws, app.state.flash, app.state.lock = ws, None, threading.RLock()
    app.state.views, app.state.banner, app.state.seen = 0, False, ""  # screen-only: session notice, notifications seen
    if task_id:
        ws.reset(task_id)
    host_key = os.environ.get("OPSDESK_HOST_KEY", "")

    @app.middleware("http")
    async def mark_source(request: Request, call_next):
        # 3.1 telemetry: tool calls made while serving a screen are GUI actions, /api/* calls are API actions
        path = request.url.path
        token = CALL_SOURCE.set("api" if path.startswith("/api/") else "host" if path.startswith("/host/") else "gui")
        try:
            return await call_next(request)
        finally:
            CALL_SOURCE.reset(token)

    def ready() -> bool:
        return ws.state is not None

    def host_ok(request: Request) -> bool:
        supplied = request.headers.get("x-opsdesk-host-key", "")
        return bool(host_key) and hmac.compare_digest(supplied, host_key)

    def localize_error(message: str) -> str:
        """Refusals as a user reads them (3.1.1): the home's language, no schema or developer text."""
        import re as _re
        loc = ws.app.locale if ws.app else "en-GB"
        de = loc == "de-DE"
        message = _re.sub(r"^Invalid arguments\.\s*(?:Value error,\s*)?", "", message).strip() or "Invalid arguments."
        if de and message in ERRORS_DE:
            return ERRORS_DE[message]
        if message.startswith("Roster has hard-rule violations"):
            return t(loc, "publish.blocked")
        if " declines " in message:
            from opsdesk.workforce.veneer import staff_reason
            who, _, reason = message.partition(" declines ")
            reason = staff_reason(loc, reason.split(": ", 1)[-1])
            return f"{who} stimmt dem Tausch nicht zu: {reason}" if de else f"{who} does not agree to the swap: {reason}"
        m = _re.match(r"Request costs ([\d.]+); only ([\d.-]+) remains in the agency budget\.", message)
        if m:
            if ws.app.budget_in_mail:  # the budget is the manager's (in her mail), the app does not show it
                return ("Diese Buchung liegt über dem freigegebenen Zeitarbeitsbudget." if de else
                        "This booking is above the agency budget released for this period.")
            cost, left = (fmt_money(loc, int(round(float(x) * 100))) for x in m.groups())
            return (f"Diese Buchung ({cost}) liegt über dem verbleibenden Zeitarbeitsbudget ({left})." if de else
                    f"This booking ({cost}) is above the agency budget left for this period ({left}).")
        if message.startswith("Not in the address book: "):
            return ("Nicht im Adressbuch: " if de else "Not in the address book: ") + message.split(": ", 1)[1]
        if "time-clock entry" in message or "entries remain immutable" in message:
            return ("Stempelzeiten können hier nicht korrigiert werden; die Korrektur bestätigt die Person am Terminal." if de else
                    "Time-clock entries can't be corrected here; the employee confirms a correction at the terminal.")
        plain = {  # pydantic and argument checks, in user words
            r"end_date must be on or after start_date": ("The end date must be on or after the start date.",
                                                         "Das Enddatum muss am oder nach dem Anfangsdatum liegen."),
            r"Input should be (?:less|greater) than or equal to ([\d.]+)": ("Please enter a value within the allowed range (limit {0}).",
                                                                           "Bitte einen Wert im erlaubten Bereich eingeben (Grenze {0})."),
            r"List should have at least 1 item": ("Please choose at least one entry.", "Bitte mindestens eine Auswahl treffen."),
            r"Field required": ("Please fill in all required fields.", "Bitte alle Pflichtfelder ausfüllen."),
            r"Input should be a valid (?:date|datetime)": ("Please enter a valid date.", "Bitte ein gültiges Datum eingeben."),
            r"Input should be a valid (?:integer|number)": ("Please enter a number.", "Bitte eine Zahl eingeben."),
            r"Input should be '": ("Please choose one of the options offered.", "Bitte eine der angebotenen Optionen wählen."),
        }
        for pattern, (en, ger) in plain.items():
            hit = _re.search(pattern, message)
            if hit:
                return (ger if de else en).format(*hit.groups())
        if message == "Invalid arguments." or ";" in message and ":" in message:
            return "Bitte die Eingaben im Formular prüfen." if de else "Please check the entries in the form."
        return message

    def flash_from(results: list[dict[str, Any]]) -> dict[str, Any]:
        loc = ws.app.locale
        failed = [r for r in results if not r.get("ok")]
        if failed:
            return {"ok": False, "title": t(loc, "error"), "details": [localize_error(r.get("message", "")) for r in failed]}
        warnings = []
        for r in results:
            for w in (r.get("data") or {}).get("warnings", []) if isinstance(r.get("data"), dict) else []:
                warnings.append(rule_text(loc, w.split(":", 1)[0]))
        if warnings:
            return {"ok": True, "title": t(loc, "changes_saved_with_warnings"), "details": sorted(set(warnings))}
        return {"ok": True, "title": t(loc, "done")}

    def badges() -> dict[str, int]:
        app.state.views += 1
        if app.state.views >= 40:  # after a long stretch of work the session notice comes up (dismissable)
            app.state.banner = True
        return {"mail": sum(1 for m in ws.app.mails if m.folder == "inbox" and not m.is_read),
                "bell": len(office_pages.notification_items(ws, app.state.seen)), "session": int(app.state.banner)}

    def render(view, *args) -> HTMLResponse:
        if not ready():
            return HTMLResponse("<!doctype html><title>OpsDesk Workforce</title><p>Starting…</p>", status_code=503)
        with app.state.lock:
            title, active, body = view(ws, *args)
            flash, app.state.flash = app.state.flash, None
            mailed = {m.message_id for m in ws.app.message_meta if m.channel == "email"}  # shown in OpsDesk Mail
            unread = sum(1 for m in ws.state.messages if m.recipient_id == "manager" and not m.is_read and m.message_id not in mailed)
            open_requests = sum(1 for r in ws.app.requests if r.status == "open")
            return HTMLResponse(page(ws.app, ws.state, title=title, active=active, body=body, flash=flash,
                                     unread=unread, open_requests=open_requests, badges=badges(), apps=apps))

    def office(view, *args) -> HTMLResponse:
        """Mail, team chat and calendar: same layout, their own header, look and left column."""
        if not ready():
            return HTMLResponse("<!doctype html><title>OpsDesk</title><p>Starting…</p>", status_code=503)
        with app.state.lock:
            title, area, body, side = view(ws, *args)
            flash, app.state.flash = app.state.flash, None
            return HTMLResponse(page(ws.app, ws.state, title=title, active="", body=body, flash=flash, area=area, side=side,
                                     badges=badges(), apps=apps))

    def done(results: list[dict[str, Any]], back: str) -> RedirectResponse:
        app.state.flash = flash_from(results)
        safe = back if back.startswith("/") and not back.startswith("//") else "/rota"
        return RedirectResponse(safe, status_code=303)

    app.state.office_view, app.state.done = office, done  # for replicas that add their own screens

    def notice(employee_id: str, d: str, ward: str, shift: str, kind: str) -> dict[str, Any]:
        loc = ws.app.locale
        name = next((x.name for x in ws.state.employees if x.employee_id == employee_id), "")
        first = name.split(" ")[0]
        label = f"{fmt_short(loc, date.fromisoformat(d))}, {ws.app.unit_names.get(ward, ward)}, {shift_name(loc, shift)}"
        texts = {
            "en-GB": {"added": ("Rota change", f"Hello {first}, you are now on this shift: {label}."),
                      "removed": ("Rota change", f"Hello {first}, you are no longer on this shift: {label}."),
                      "approved": ("Your swap request", f"Hello {first}, the swap was approved. Your shift: {label}."),
                      "declined": ("Your swap request", f"Hello {first}, the swap was declined. Your shift stays: {label}.")},
            "de-DE": {"added": ("Dienstplanänderung", f"Hallo {first}, du bist jetzt in diesem Dienst eingeplant: {label}."),
                      "removed": ("Dienstplanänderung", f"Hallo {first}, du bist aus diesem Dienst ausgeplant: {label}."),
                      "approved": ("Dein Tauschwunsch", f"Hallo {first}, der Tausch ist genehmigt. Dein Dienst: {label}."),
                      "declined": ("Dein Tauschwunsch", f"Hallo {first}, der Tausch ist abgelehnt. Dein Dienst bleibt: {label}.")},
        }[loc][kind]
        sign = f"\n{ws.app.home.user_name}"
        return ws.step("messaging.send", {"recipient_id": employee_id, "subject": texts[0], "body": texts[1] + sign,
                                          "shift_date": d, "shift_type": shift, "ward_id": ward})

    # ------------------------------------------------------------------ static and GUI pages
    @app.get("/static/app.css", include_in_schema=False)
    def css() -> Response:
        return Response((STATIC / "app.css").read_text(), media_type="text/css")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/rota", status_code=303)

    @app.get("/rota", response_class=HTMLResponse)
    def rota(request: Request):
        return render(pages.rota, dict(request.query_params))

    @app.get("/rota/remove", response_class=HTMLResponse)
    def rota_remove_confirm(request: Request):
        return render(pages.remove_confirm, dict(request.query_params))

    @app.get("/staff", response_class=HTMLResponse)
    def staff(request: Request):
        return render(pages.staff, dict(request.query_params))

    @app.get("/staff/{employee_id}", response_class=HTMLResponse)
    def profile(employee_id: str, request: Request):
        return render(pages.profile, employee_id, dict(request.query_params))

    @app.get("/absences", response_class=HTMLResponse)
    def absences(request: Request):
        return render(pages.absences, dict(request.query_params))

    @app.get("/absences/new", response_class=HTMLResponse)
    def absence_new():
        return render(pages.absence_form, None)

    @app.get("/absences/{absence_id}/edit", response_class=HTMLResponse)
    def absence_edit(absence_id: str):
        return render(pages.absence_form, absence_id)

    @app.get("/absences/{absence_id}/delete", response_class=HTMLResponse)
    def absence_delete_page(absence_id: str):
        return render(pages.absence_delete, absence_id)

    @app.get("/requests", response_class=HTMLResponse)
    def requests_page(request: Request):
        return render(pages.requests, dict(request.query_params))

    @app.get("/requests/{request_id}", response_class=HTMLResponse)
    def request_detail(request_id: str):
        return render(pages.request_detail, request_id)

    @app.get("/messages", response_class=HTMLResponse)
    def messages(request: Request):
        return render(pages.messages, dict(request.query_params))

    @app.get("/messages/new", response_class=HTMLResponse)
    def message_new(request: Request):
        return render(pages.message_new, dict(request.query_params))

    @app.get("/messages/{message_id}", response_class=HTMLResponse)
    def message_detail(message_id: str):
        return render(pages.message_detail, message_id)

    # 3.1 office apps -----------------------------------------------------------------------------
    @app.get("/mail", response_class=HTMLResponse)
    def mail_list(request: Request):
        return office(office_pages.mail_list, dict(request.query_params))

    @app.get("/mail/compose", response_class=HTMLResponse)
    def mail_compose(request: Request):
        return office(office_pages.mail_compose, dict(request.query_params))

    @app.get("/mail/contacts", response_class=HTMLResponse)
    def mail_contacts(request: Request):
        return office(office_pages.mail_contacts, dict(request.query_params))

    @app.get("/mail/attachment/{attachment_id}")
    def mail_attachment(attachment_id: str, request: Request):
        if request.query_params.get("view"):
            return office(office_pages.attachment_view, attachment_id)
        with app.state.lock:
            result = ws.step("mail.attachment", {"attachment_id": attachment_id})
        if not result.get("ok"):
            return PlainTextResponse("Not found", status_code=404)
        data = result["data"]
        if data["media_type"] == "application/pdf":
            from opsdesk.workforce.pdf import render as render_pdf
            return Response(render_pdf(data["text"], data["filename"]), media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{data["filename"]}"'})
        return Response(data["text"].encode("utf-8"), media_type=f'{data["media_type"]}; charset=utf-8',
                        headers={"Content-Disposition": f'attachment; filename="{data["filename"]}"'})

    @app.get("/mail/{mail_id}", response_class=HTMLResponse)
    def mail_detail(mail_id: str):
        return office(office_pages.mail_detail, mail_id)

    @app.post("/mail/send")
    async def mail_send(request: Request):
        f = _form(await request.body())
        split = lambda v: [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]  # noqa: E731
        args: dict[str, Any] = {"to": split(_one(f, "to")), "cc": split(_one(f, "cc")), "subject": _one(f, "subject"),
                                "body": _one(f, "body")}
        if _one(f, "in_reply_to"):
            args["in_reply_to"] = _one(f, "in_reply_to")
        with app.state.lock:
            results = [ws.step("mail.send", args)]
        if results[0].get("ok"):
            app.state.flash = {"ok": True, "title": t(ws.app.locale, "mail.sent_ok")}
            return RedirectResponse("/mail?folder=sent", status_code=303)
        return done(results, "/mail/compose" + (f"?reply={args['in_reply_to']}" if args.get("in_reply_to") else ""))

    @app.get("/chat", response_class=HTMLResponse)
    def chat(request: Request):
        return office(office_pages.chat, dict(request.query_params))

    @app.post("/chat/post")
    async def chat_post(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {"text": _one(f, "text")}
        for key in ("channel_id", "employee_id", "shift_date", "ward_id", "shift_type"):
            if _one(f, key):
                args[key] = _one(f, key)
        if _one(f, "ask"):
            args["ask"] = True
        with app.state.lock:
            results = [ws.step("chat.post", args)]
        channel = (results[0].get("data") or {}).get("channel_id") or args.get("channel_id", "")
        return done(results, f"/chat?c={channel}" if channel else "/chat")

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request):
        return office(office_pages.calendar_week, dict(request.query_params))

    @app.get("/calendar/new", response_class=HTMLResponse)
    def calendar_new(request: Request):
        return office(office_pages.calendar_new, dict(request.query_params))

    @app.get("/calendar/{event_id}", response_class=HTMLResponse)
    def calendar_event(event_id: str):
        return office(office_pages.calendar_event, event_id)

    @app.post("/calendar/new")
    async def calendar_create(request: Request):
        f = _form(await request.body())
        args = {k: _one(f, k) for k in ("title", "kind", "start", "end", "location", "notes")}
        for side in ("start", "end"):  # the form has a date and a time field; the tool takes "YYYY-MM-DD HH:MM"
            if not args[side] and _one(f, f"{side}_date"):
                args[side] = f"{_one(f, f'{side}_date')} {_one(f, f'{side}_time') or '00:00'}"
        args["attendees"] = f.get("attendees", [])
        with app.state.lock:
            results = [ws.step("calendar.create", args)]
        if results[0].get("ok"):
            app.state.flash = {"ok": True, "title": t(ws.app.locale, "cal.saved")}
            return RedirectResponse(url("/calendar", start=args["start"][:10]), status_code=303)
        return done(results, "/calendar/new")

    @app.get("/agency", response_class=HTMLResponse)
    def agency(request: Request):
        return render(pages.agency, dict(request.query_params))

    @app.get("/publish", response_class=HTMLResponse)
    def publish_page(request: Request):
        return render(pages.publish, dict(request.query_params))

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request):
        return render(pages.reports, dict(request.query_params))

    @app.get("/reports/understaffing/new", response_class=HTMLResponse)
    def understaffing_new(request: Request):
        return render(pages.understaffing_new, dict(request.query_params))

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request):
        return render(pages.audit, dict(request.query_params))

    @app.get("/documents", response_class=HTMLResponse)
    def documents(request: Request):
        return render(pages.documents, dict(request.query_params))

    @app.get("/documents/{document_id}", response_class=HTMLResponse)
    def document_detail(document_id: str):
        return render(pages.document_detail, document_id)

    @app.get("/timeclock", response_class=HTMLResponse)
    def timeclock(request: Request):
        return render(pages.timeclock, dict(request.query_params))

    @app.get("/payroll", response_class=HTMLResponse)
    def payroll(request: Request):
        return render(pages.payroll, dict(request.query_params))

    @app.get("/council", response_class=HTMLResponse)
    def council(request: Request):
        return render(pages.council, dict(request.query_params))

    # ------------------------------------------------------------------ GUI actions (each = tool calls)
    @app.post("/rota/assign")
    async def rota_assign(request: Request):
        f = _form(await request.body())
        args = {k: _one(f, k) for k in ("employee_id", "date", "ward_id", "shift_type")}
        with app.state.lock:
            results = [ws.step("roster.assign", args)]
            if results[0].get("ok") and _one(f, "notify"):
                results.append(notice(args["employee_id"], args["date"], args["ward_id"], args["shift_type"], "added"))
        return done(results, _one(f, "back", "/rota"))

    @app.post("/rota/unassign")
    async def rota_unassign(request: Request):
        f = _form(await request.body())
        if not _one(f, "confirmed"):  # 3.1: removing someone from a shift asks first, as real rota software does
            from urllib.parse import urlencode
            back = _one(f, "back", "/rota")
            query = urlencode({"assignment_id": _one(f, "assignment_id"), "notify": _one(f, "notify"), "back": back})
            return RedirectResponse(f"/rota/remove?{query}", status_code=303)
        with app.state.lock:
            a = next((x for x in ws.state.roster.assignments if x.assignment_id == _one(f, "assignment_id")), None)
            results = [ws.step("roster.unassign", {"assignment_id": _one(f, "assignment_id")})]
            if results[0].get("ok") and a is not None and _one(f, "notify"):
                results.append(notice(a.employee_id, a.date.isoformat(), a.ward_id, a.shift_type.value, "removed"))
        return done(results, _one(f, "back", "/rota"))

    @app.post("/rota/swap")
    async def rota_swap(request: Request):
        f = _form(await request.body())
        with app.state.lock:
            results = [ws.step("roster.swap", {"first_assignment_id": _one(f, "first_assignment_id"),
                                               "second_assignment_id": _one(f, "second_assignment_id")})]
        return done(results, _one(f, "back", "/rota"))

    @app.post("/staff/{employee_id}/edit")
    async def staff_edit(employee_id: str, request: Request):
        f = _form(await request.body())
        with app.state.lock:
            x = next((e for e in ws.state.employees if e.employee_id == employee_id), None)
            prof = ws.app.profile(employee_id)
            if x is None:
                return done([{"ok": False, "message": "Employee not found."}], "/staff")
            args: dict[str, Any] = {"employee_id": employee_id}
            if prof is not None and _one(f, "phone") != prof.phone:
                args["phone"] = _one(f, "phone")
            if prof is not None and _one(f, "notes") != prof.notes:
                args["notes"] = _one(f, "notes")
            try:
                hours = float(_one(f, "contract_hours_per_week", str(x.contract.hours_per_week)).replace(",", "."))
            except ValueError:
                hours = -1.0
            if hours != x.contract.hours_per_week:
                args["contract_hours_per_week"] = hours
            quals = sorted(f.get("qualifications", []))
            if quals != sorted(q.value for q in x.qualifications):
                args["qualifications"] = quals
            results = [ws.step("hr.update_employee", args)] if len(args) > 1 else [{"ok": True}]
        return done(results, f"/staff/{employee_id}")

    @app.post("/absences/new")
    async def absence_create(request: Request):
        f = _form(await request.body())
        with app.state.lock:
            results = [ws.step("absences.record", {k: _one(f, k) for k in ("employee_id", "absence_type", "start_date",
                                                                            "end_date", "note")})]
        return done(results, "/absences")

    @app.post("/absences/{absence_id}/edit")
    async def absence_update(absence_id: str, request: Request):
        f = _form(await request.body())
        with app.state.lock:
            a = next((x for x in ws.state.absences if x.absence_id == absence_id), None)
            meta = next((m for m in ws.app.absence_meta if m.absence_id == absence_id), None)
            args: dict[str, Any] = {"absence_id": absence_id}
            if a is not None:
                for key, current in (("absence_type", a.absence_type.value), ("start_date", a.start_date.isoformat()),
                                     ("end_date", a.end_date.isoformat())):
                    if _one(f, key, current) != current:
                        args[key] = _one(f, key)
                if _one(f, "note") != (meta.note if meta else ""):
                    args["note"] = _one(f, "note")
            results = [ws.step("absences.update", args)] if len(args) > 1 or a is None else [{"ok": True}]
        return done(results, "/absences")

    @app.post("/absences/{absence_id}/delete")
    async def absence_delete(absence_id: str):
        with app.state.lock:
            results = [ws.step("absences.delete", {"absence_id": absence_id})]
        return done(results, "/absences")

    @app.post("/requests/{request_id}/decide")
    async def request_decide(request_id: str, request: Request):
        f = _form(await request.body())
        with app.state.lock:
            r = next((x for x in ws.app.requests if x.request_id == request_id), None)
            by_id = {a.assignment_id: a for a in ws.state.roster.assignments}
            before = {k: by_id.get(getattr(r, k)) for k in ("first_assignment_id", "second_assignment_id")} if r else {}
            positions = {a.employee_id: (a.date.isoformat(), a.ward_id, a.shift_type.value) for a in before.values() if a}
            results = [ws.step("requests.decide", {"request_id": request_id, "decision": _one(f, "decision"),
                                                   "reason": _one(f, "reason")})]
            if results[0].get("ok") and _one(f, "notify") and r is not None:
                approved = _one(f, "decision") == "approve"
                for person, other in ((r.requester_id, r.partner_id), (r.partner_id, r.requester_id)):
                    # after an approved swap each person works the other one's former shift
                    where = positions.get(other if approved else person)
                    if where:
                        results.append(notice(person, *where, "approved" if approved else "declined"))
        return done(results, f"/requests/{request_id}")

    @app.post("/messages/send")
    async def message_send(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("recipient_id", "subject", "body")}
        if _one(f, "shift_date") or _one(f, "ward_id") or _one(f, "shift_type"):
            args.update({k: _one(f, k) for k in ("shift_date", "ward_id", "shift_type")})
            if _one(f, "request_intent"):
                args["request_intent"] = True
        with app.state.lock:
            results = [ws.step("messaging.send", args)]
        return done(results, "/messages?box=sent")

    @app.post("/agency/request")
    async def agency_request(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("offer_id", "requested_for", "ward_id", "shift_type")}
        try:
            args["staff_count"] = int(_one(f, "staff_count", "1"))
        except ValueError:
            args["staff_count"] = _one(f, "staff_count")
        with app.state.lock:
            results = [ws.step("agency.request", args)]
        return done(results, "/agency")

    @app.post("/agency/{request_id}/cancel")
    async def agency_cancel(request_id: str):
        with app.state.lock:
            results = [ws.step("agency.cancel_request", {"request_id": request_id})]
        return done(results, "/agency")

    @app.post("/publish")
    async def publish(request: Request):
        f = _form(await request.body())
        with app.state.lock:
            results = [ws.step("roster.publish", {})]
            if results[0].get("ok") and _one(f, "notify"):
                told = pages.notified_positions(ws)
                current = {(a.employee_id, a.date, a.ward_id, a.shift_type.value) for a in ws.state.roster.assignments}
                for emp, d, ward, shift in pages.changed_positions(ws):
                    if (emp, d.isoformat(), ward, shift) not in told:
                        kind = "added" if (emp, d, ward, shift) in current else "removed"
                        results.append(notice(emp, d.isoformat(), ward, shift, kind))
        return done(results, "/publish")

    @app.post("/reports/understaffing")
    async def understaffing(request: Request):
        f = _form(await request.body())
        items = []
        try:
            count = int(_one(f, "count", "0"))
        except ValueError:
            count = 0
        for i in range(min(count, 400)):
            if _one(f, f"include_{i}"):
                items.append({"date": _one(f, f"date_{i}"), "ward_id": _one(f, f"ward_{i}"), "shift_type": _one(f, f"shift_{i}"),
                              "missing": _one(f, f"missing_{i}"), "reason": _one(f, f"reason_{i}")})
        if _one(f, "date_x"):
            items.append({"date": _one(f, "date_x"), "ward_id": _one(f, "ward_x"), "shift_type": _one(f, "shift_x"),
                          "missing": _one(f, "missing_x"), "reason": _one(f, "reason_x")})
        with app.state.lock:
            results = [ws.step("reports.understaffing", {"shifts": items, "note": _one(f, "note")})]
        return done(results, "/reports/understaffing/new")

    @app.post("/reports/{report_id}/withdraw")
    async def withdraw(report_id: str):
        with app.state.lock:
            results = [ws.step("reports.withdraw_understaffing", {"report_id": report_id})]
        return done(results, "/reports/understaffing/new")

    @app.post("/timeclock/correct")
    async def timeclock_correct(request: Request):
        f = _form(await request.body())
        with app.state.lock:
            results = [ws.step("timeclock.attempt_edit", {"entry_id": _one(f, "entry_id")})]
        return done(results, "/timeclock")

    @app.post("/payroll/export")
    async def payroll_export(request: Request):
        f = _form(await request.body())
        with app.state.lock:
            results = [ws.step("payroll.export", {"start_date": _one(f, "start_date"), "end_date": _one(f, "end_date")})]
        return done(results, "/payroll")

    @app.post("/council/submit")
    async def council_submit(request: Request):
        f = _form(await request.body())
        if not _one(f, "confirmed"):  # 3.1: submitting to the works council asks first
            return RedirectResponse("/council?confirm=1", status_code=303)
        with app.state.lock:
            results = [ws.step("works_council.submit", {})]
        return done(results, "/council")

    # 3.1 realism: notification centre and session notice (screen-only, not part of the workspace state) ---------
    @app.get("/notifications", response_class=HTMLResponse)
    def notifications(request: Request):
        if request.query_params.get("seen"):
            with app.state.lock:
                app.state.seen = ws.app.now(ws.state.current_date)
            return RedirectResponse("/notifications", status_code=303)
        return office(office_pages.notifications, app.state.seen)

    @app.get("/session/stay")
    def session_stay(request: Request):
        app.state.views, app.state.banner = 0, False
        back = request.query_params.get("back", "/rota")
        return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/rota", status_code=303)

    # ------------------------------------------------------------------ tool API (same session)
    @app.get("/api/tools")
    def api_tools():
        if not ready():
            return JSONResponse({"ok": False, "error_code": "NOT_READY", "message": "Workspace not loaded."}, 503)
        return {"tools": ws.tool_schemas()}

    @app.get("/api/state")
    def api_state():
        if not ready():
            return JSONResponse({"ok": False, "error_code": "NOT_READY", "message": "Workspace not loaded."}, 503)
        return ws.observation()

    @app.post("/api/step")
    async def api_step(request: Request):
        if not ready():
            return JSONResponse({"ok": False, "error_code": "NOT_READY", "message": "Workspace not loaded."}, 503)
        try:
            body = await request.json()
            name, args = body["tool"], body.get("args", {})
        except Exception:
            return JSONResponse({"ok": False, "error_code": "INVALID_REQUEST",
                                 "message": "Send JSON {\"tool\": name, \"args\": {...}}."}, 400)
        if not isinstance(name, str) or not isinstance(args, dict):
            return JSONResponse({"ok": False, "error_code": "INVALID_REQUEST", "message": "tool must be a string, args an object."}, 400)
        with app.state.lock:
            return ws.step(name, args)

    # ------------------------------------------------------------------ host only
    @app.post("/host/reset")
    async def host_reset(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        body = await request.json()
        with app.state.lock:
            try:
                ws.reset(str(body.get("task_id", "")))
            except (FileNotFoundError, KeyError, ValueError):
                return JSONResponse({"ok": False, "error_code": "UNKNOWN_TASK"}, 404)
            # screen-only state starts fresh too (3.1.1): no session notice, no notifications marked as seen
            app.state.flash, app.state.views, app.state.banner, app.state.seen = None, 0, False, ""
        return {"ok": True, "state_hash": ws.state_hash()}

    @app.post("/host/finish")
    def host_finish(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        with app.state.lock:
            return ws.finish()

    @app.get("/host/state-hash")
    def host_hash(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        return {"state_hash": ws.state_hash()}

    @app.get("/host/export")
    def host_export(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        with app.state.lock:
            out = ws.export()
            out["app_state"] = ws.app.model_dump(mode="json")
            return out

    @app.post("/host/snapshot")
    def host_snapshot(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        with app.state.lock:
            return {"ok": True, **ws.snapshot()}

    @app.post("/host/restore")
    async def host_restore(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        body = await request.json()
        with app.state.lock:
            if ws.finished is not None:  # a finished workspace keeps its score
                return JSONResponse({"ok": False, "error_code": "WORKSPACE_FINISHED"}, 409)
            try:
                return {"ok": True, **ws.restore(int(body.get("snapshot_id", -1)))}
            except (IndexError, ValueError, TypeError):
                return JSONResponse({"ok": False, "error_code": "UNKNOWN_SNAPSHOT"}, 404)

    @app.get("/host/telemetry")
    def host_telemetry(request: Request):
        if not host_ok(request):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        with app.state.lock:
            return PlainTextResponse(ws.telemetry_jsonl(), media_type="application/x-ndjson")

    @app.get("/healthz", include_in_schema=False)
    def health() -> PlainTextResponse:
        return PlainTextResponse("ok" if ready() else "starting")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="OpsDesk Workforce server (one workspace)")
    parser.add_argument("--tasks-root", default=os.environ.get("OPSDESK_TASKS_ROOT", "tasks"))
    parser.add_argument("--task", default=os.environ.get("OPSDESK_TASK"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(create_workforce_app(args.tasks_root, args.task), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
