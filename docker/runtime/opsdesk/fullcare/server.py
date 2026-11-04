"""OpsDesk FullCare server: the Workforce workspace (rota, mail, chat, calendar, host routes, tool API) with a
`CareSession`, plus the care record and the daily plan screens.

    OPSDESK_TASKS_ROOT=<tasks dir> OPSDESK_TASK=<task folder> OPSDESK_HOST_KEY=<key> \
        python -m opsdesk.fullcare.server --port 8080

Tools (care, plan, rota, mail, …) are at `/api/tools` and `/api/step`, as in Workforce, so the Workforce MCP bridge
works unchanged (`python -m opsdesk.fullcare.mcp`).
"""

from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from opsdesk.fullcare import gui
from opsdesk.fullcare import i18n as _texts  # noqa: F401 (registers the app-switcher labels)
from opsdesk.fullcare.session import CareSession, assert_unique_names
from opsdesk.workforce.gui.html import APPS
from opsdesk.workforce.server import STATIC, _form, _one, create_workforce_app

FULLCARE_APPS = (("care", "/care/residents", "FC"), ("plan", "/plan", "FC")) + APPS
CARE_CSS = Path(__file__).parent / "care.css"


def _num(text: str) -> float | str:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return text  # the tool refuses it with a readable message


def create_fullcare_app(tasks_root: str | Path, task_id: str | None = None) -> FastAPI:
    app = create_workforce_app(tasks_root, None, session_factory=CareSession, apps=FULLCARE_APPS, title="OpsDesk FullCare")
    ws: CareSession = app.state.ws
    office, done = app.state.office_view, app.state.done
    # 0.6.4: FullCare's own /host/finish (below) – a pending score under the evaluation licence instead of an error
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") not in ("/", "/static/app.css", "/host/finish")]
    if task_id:
        ws.reset(task_id)
    assert_unique_names(ws.tool_schemas(all_tools=True))  # refuse to start with a name listed twice
    host_key = os.environ.get("OPSDESK_HOST_KEY", "")

    def back(f: dict[str, list[str]], default: str = "/care/residents") -> str:
        return _one(f, "back", default)

    def step(name: str, args: dict[str, Any]) -> dict[str, Any]:
        with app.state.lock:
            return ws.step(name, args)

    @app.get("/static/app.css", include_in_schema=False)
    def css() -> Response:
        return Response((STATIC / "app.css").read_text() + "\n" + CARE_CSS.read_text(), media_type="text/css")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/care/residents", status_code=303)

    # ------------------------------------------------------------------ screens
    @app.get("/care/residents", response_class=HTMLResponse)
    def residents(request: Request):
        return office(gui.residents, dict(request.query_params))

    @app.get("/care/residents/{resident_id}", response_class=HTMLResponse)
    def record(resident_id: str, request: Request):
        return office(gui.record, resident_id, dict(request.query_params))

    @app.get("/care/residents/{resident_id}/assess", response_class=HTMLResponse)
    def assess(resident_id: str, request: Request):
        return office(gui.assessment_form, resident_id, dict(request.query_params))

    @app.get("/care/orders", response_class=HTMLResponse)
    def orders(request: Request):
        return office(gui.orders, dict(request.query_params))

    @app.get("/care/standard", response_class=HTMLResponse)
    def standard(request: Request):
        return office(gui.standard, dict(request.query_params))

    @app.get("/plan", response_class=HTMLResponse)
    def plan(request: Request):
        return office(gui.plan, dict(request.query_params))

    # ------------------------------------------------------------------ forms (one tool call each)
    @app.post("/care/observation")
    async def observation(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {"resident_id": _one(f, "resident_id"), "kind": _one(f, "kind"), "value": _num(_one(f, "value")),
                                "unit": _one(f, "unit"), "measured_at": _one(f, "measured_at")}
        for key in ("measured_by", "source_ref", "note"):
            if _one(f, key).strip():
                args[key] = _one(f, key).strip()
        if _one(f, "value2").strip():
            args["value2"] = _num(_one(f, "value2"))
        return done([step("care.observation_record", args)], back(f))

    @app.post("/care/fluid")
    async def fluid(request: Request):
        f = _form(await request.body())
        ml = _one(f, "ml")
        args = {"resident_id": _one(f, "resident_id"), "direction": _one(f, "direction"), "kind": _one(f, "kind"),
                "ml": int(ml) if ml.isdigit() else ml, "at": _one(f, "at")}
        if _one(f, "source_ref").strip():
            args["source_ref"] = _one(f, "source_ref").strip()
        return done([step("care.fluid_record", args)], back(f))

    @app.post("/care/excretion")
    async def excretion(request: Request):
        f = _form(await request.body())
        return done([step("care.excretion_record", {"resident_id": _one(f, "resident_id"), "kind": _one(f, "kind"),
                                                    "continence": _one(f, "continence"), "at": _one(f, "at"),
                                                    "note": _one(f, "note")})], back(f))

    @app.post("/care/assessment")
    async def assessment(request: Request):
        f = _form(await request.body())
        answers = {k[2:]: v[0] for k, v in f.items() if k.startswith("a.")}
        return done([step("care.assessment_submit", {"resident_id": _one(f, "resident_id"), "scale_id": _one(f, "scale_id"),
                                                     "answers": answers, "performed_at": _one(f, "performed_at"),
                                                     "source_refs": [x.strip() for x in _one(f, "source_refs").split(",") if x.strip()],
                                                     "note": _one(f, "note")})], back(f))

    @app.post("/care/risk")
    async def risk(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {"resident_id": _one(f, "resident_id"), "kind": _one(f, "kind"),
                                "present": _one(f, "present") == "1", "reason": _one(f, "reason")}
        if _one(f, "review_on"):
            args["review_on"] = _one(f, "review_on")
        return done([step("care.risk_set", args)], back(f))

    @app.post("/care/careplan")
    async def careplan_entry(request: Request):
        f = _form(await request.body())
        args = {k: _one(f, k) for k in ("resident_id", "theme", "finding", "problem", "resource", "goal", "review_on")
                if _one(f, k).strip()}
        return done([step("care.careplan_entry_add", args)], back(f))

    @app.post("/care/careplan/update")
    async def careplan_update(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {"entry_id": _one(f, "entry_id")}
        if _one(f, "review_on"):
            args["review_on"] = _one(f, "review_on")
        if _one(f, "status"):
            args["status"] = _one(f, "status")
        return done([step("care.careplan_entry_update", args)], back(f))

    @app.post("/care/measure")
    async def measure(request: Request):
        f = _form(await request.body())
        schedule: dict[str, Any] = {"kind": _one(f, "schedule_kind", "times"),
                                    "times": [t.strip() for t in _one(f, "times").split(",") if t.strip()]}
        for key in ("every_hours", "night_every_hours"):
            if _one(f, key).strip():
                schedule[key] = int(_one(f, key)) if _one(f, key).isdigit() else _one(f, key)
        if _one(f, "weekdays").strip():
            schedule["weekdays"] = [int(x) if x.strip().isdigit() else x for x in _one(f, "weekdays").split(",")]
        args: dict[str, Any] = {"resident_id": _one(f, "resident_id"), "catalogue_key": _one(f, "catalogue_key"),
                                "schedule": schedule, "start": _one(f, "start")}
        for key in ("end", "entry_id", "order_id"):
            if _one(f, key):
                args[key] = _one(f, key)
        return done([step("care.measure_add", args)], back(f))

    @app.post("/care/measure/end")
    async def measure_end(request: Request):
        f = _form(await request.body())
        return done([step("care.measure_end", {"measure_id": _one(f, "measure_id"), "end": _one(f, "end"),
                                               "reason": _one(f, "reason")})], back(f))

    @app.post("/care/report")
    async def report(request: Request):
        f = _form(await request.body())
        informed = [x.strip() for x in _one(f, "informed").split(",") if x.strip()]
        return done([step("care.report_write", {"resident_id": _one(f, "resident_id"), "category": _one(f, "category"),
                                                "text": _one(f, "text"), "informed": informed,
                                                "observation_ids": f.get("observation_ids", [])})], back(f))

    @app.post("/care/acknowledge")
    async def acknowledge(request: Request):
        f = _form(await request.body())
        return done([step("care.order_acknowledge", {"order_id": _one(f, "order_id")})], back(f, "/care/orders"))

    @app.post("/care/appointment")
    async def appointment(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("resident_id", "kind", "date", "start", "end", "place", "with_whom")}
        args["transport_needed"] = _one(f, "transport_needed") == "1"
        if _one(f, "escort_qualification"):
            args["escort_qualification"] = _one(f, "escort_qualification")
        return done([step("care.appointment_create", args)], back(f))

    @app.post("/care/appointment/cancel")
    async def appointment_cancel(request: Request):
        f = _form(await request.body())
        return done([step("care.appointment_cancel", {"appointment_id": _one(f, "appointment_id"),
                                                      "reason": _one(f, "reason")})], back(f))

    @app.post("/plan/assign")
    async def plan_assign(request: Request):
        f = _form(await request.body())
        return done([step("plan.assign", {"item_key": _one(f, "item_key"), "employee_id": _one(f, "employee_id") or None})],
                    back(f, "/plan"))

    @app.post("/plan/status")
    async def plan_status(request: Request):
        f = _form(await request.body())
        return done([step("plan.set_status", {"item_key": _one(f, "item_key"), "status": _one(f, "status"),
                                              "reason": _one(f, "reason")})], back(f, "/plan"))

    @app.post("/plan/add")
    async def plan_add(request: Request):
        f = _form(await request.body())
        return done([step("plan.add_item", {k: _one(f, k) for k in ("resident_id", "date", "due", "catalogue_key", "reason")})],
                    back(f, "/plan"))

    @app.get("/care/admit", response_class=HTMLResponse)
    def admit_page(request: Request):
        return office(gui.admit_form, dict(request.query_params))

    @app.get("/care/documents/{document_id}")
    def document_pdf(document_id: str):
        with app.state.lock:
            found = gui.document_pdf(ws, document_id) if ws.care is not None else None
        if found is None:
            return Response("Not found", status_code=404)
        data, title = found
        return Response(data, media_type="application/pdf", headers={"content-disposition": f'inline; filename="{document_id}.pdf"'})

    @app.get("/print", response_class=HTMLResponse)
    def print_page(request: Request):
        return office(gui.print_queue, dict(request.query_params))

    @app.get("/care/rounds", response_class=HTMLResponse)
    def rounds_page(request: Request):
        return office(gui.rounds, dict(request.query_params))

    @app.get("/care/handover", response_class=HTMLResponse)
    def handover_page(request: Request):
        return office(gui.handover_form, dict(request.query_params))

    @app.get("/care/escalate", response_class=HTMLResponse)
    def escalate_page(request: Request):
        return office(gui.escalate_form, dict(request.query_params))

    @app.post("/care/admit")
    async def admit(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k).strip() for k in ("given_name", "family_name", "sex", "birth_date", "birth_place",
                                                                "ward_id", "room", "gp_id", "religion", "funding_note")}
        args = {k: v for k, v in args.items() if v or k in ("given_name", "family_name", "birth_place", "room")}
        if _one(f, "care_level"):
            args["care_level"] = int(_one(f, "care_level")) if _one(f, "care_level").isdigit() else _one(f, "care_level")
        args["advance_directive"] = _one(f, "advance_directive") == "1"
        res = step("care.admit", args)
        target = f"/care/residents/{res['data']['resident']['resident_id']}" if res.get("ok") else "/care/admit"
        return done([res], target)

    @app.post("/care/status")
    async def status(request: Request):
        f = _form(await request.body())
        return done([step("care.status_set", {k: _one(f, k) for k in ("resident_id", "status", "reason")})], back(f))

    @app.post("/care/contact")
    async def contact(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("resident_id", "name", "relation", "phone", "mail", "representation")}
        args["emergency"] = _one(f, "emergency") == "1"
        if _one(f, "consent_document_id"):
            args["consent_document_id"] = _one(f, "consent_document_id")
        return done([step("care.contact_add", args)], back(f))

    @app.post("/care/allergy")
    async def allergy(request: Request):
        f = _form(await request.body())
        return done([step("care.allergy_add", {k: _one(f, k) for k in ("resident_id", "substance", "reaction", "severity",
                                                                      "source_document_id")})], back(f))

    @app.post("/care/wound")
    async def wound(request: Request):
        f = _form(await request.body())
        return done([step("care.wound_create", {k: _one(f, k) for k in ("resident_id", "location", "kind", "category",
                                                                       "first_seen", "origin")})], back(f))

    @app.post("/care/wound/assess")
    async def wound_assess(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("wound_id", "at", "exudate", "wound_bed", "dressing")}
        for key in ("length_mm", "width_mm", "depth_mm"):
            args[key] = int(_one(f, key)) if _one(f, key).isdigit() else _one(f, key)
        if _one(f, "next_change"):
            args["next_change"] = _one(f, "next_change")
        args["photo_taken"], args["healed"] = _one(f, "photo_taken") == "1", _one(f, "healed") == "1"
        return done([step("care.wound_assess", args)], back(f))

    @app.post("/care/document")
    async def document(request: Request):
        f = _form(await request.body())
        return done([step("care.document_file", {k: _one(f, k) for k in ("attachment_id", "resident_id", "kind", "title")})], back(f))

    @app.post("/care/transfer")
    async def transfer(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {k: _one(f, k) for k in ("resident_id", "destination", "reason", "mobility", "continence",
                                                        "orientation", "contact_for_questions")}
        args["aids_sent"] = f.get("aids_sent", [])
        args["isolation"] = _one(f, "isolation") == "1"
        return done([step("care.transfer_sheet_save", args)], back(f))

    @app.post("/print/submit")
    async def print_submit(request: Request):
        f = _form(await request.body())
        args: dict[str, Any] = {"kind": _one(f, "kind"), "printer": _one(f, "printer"), "ref": _one(f, "ref"),
                                "copies": int(_one(f, "copies", "1")) if _one(f, "copies", "1").isdigit() else _one(f, "copies")}
        if _one(f, "resident_id"):
            args["resident_id"] = _one(f, "resident_id")
        return done([step("print.submit", args)], back(f, "/print"))

    @app.post("/print/cancel")
    async def print_cancel(request: Request):
        f = _form(await request.body())
        return done([step("print.cancel", {"job_id": _one(f, "job_id")})], back(f, "/print"))

    @app.post("/care/rounds")
    async def round_plan(request: Request):
        f = _form(await request.body())
        residents = [{"resident_id": k[2:], "points": v} for k, v in sorted(f.items()) if k.startswith("p.") and v]
        args = {"physician_id": _one(f, "physician_id"), "date": _one(f, "date"), "start": _one(f, "start"),
                "escort_id": _one(f, "escort_id"), "residents": residents}
        return done([step("care.round_plan", args)], back(f, "/care/rounds"))

    @app.post("/care/escalate")
    async def escalate(request: Request):
        f = _form(await request.body())
        return done([step("care.escalate", {"subject": _one(f, "subject"), "text": _one(f, "text"),
                                            "resident_ids": f.get("resident_ids", [])})], back(f, "/care/escalate"))

    @app.post("/care/handover")
    async def handover(request: Request):
        f = _form(await request.body())
        return done([step("care.handover_write", {"ward_id": _one(f, "ward_id"), "shift": _one(f, "shift", "early"),
                                                  "text": _one(f, "text"), "resident_ids": f.get("resident_ids", [])})],
                    back(f, "/care/handover"))

    @app.post("/shift/wait")
    async def wait(request: Request):
        f = _form(await request.body())
        return done([step("shift.wait", {"until": _one(f, "until")})], back(f, "/care/handover"))

    # ------------------------------------------------------------------ host only
    @app.get("/host/care-state")
    def host_care(request: Request):
        supplied = request.headers.get("x-opsdesk-host-key", "")
        if not (host_key and hmac.compare_digest(supplied, host_key)):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        with app.state.lock:
            return ws.export_care()

    @app.post("/host/finish")
    def host_finish(request: Request):
        """The deterministic score with the reference; without it (evaluation licence) a pending score 0.0 (0.6.4)."""
        supplied = request.headers.get("x-opsdesk-host-key", "")
        if not (host_key and hmac.compare_digest(supplied, host_key)):
            return JSONResponse({"ok": False, "error_code": "HOST_KEY_REQUIRED"}, 401)
        from opsdesk.fullcare.licence import finish
        with app.state.lock:
            return finish(ws)

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="OpsDesk FullCare server (one workspace)")
    parser.add_argument("--tasks-root", default=os.environ.get("OPSDESK_TASKS_ROOT", "tasks"))
    parser.add_argument("--task", default=os.environ.get("OPSDESK_TASK"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(create_fullcare_app(args.tasks_root, args.task), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
