"""One OpsDesk FullCare workspace: the Workforce workspace (rota, mail, chat, calendar) plus the care record.

The care state comes from the task bundle's `care.json`; a Workforce bundle without one gets a care world generated
from its seed (Phase 1: every Workforce task opens in FullCare). Care tools go through the same `step` as every
other tool, so GUI, HTTP and MCP calls are logged, counted and hashed alike. Scoring (`finish`) is still the
Workforce verifier; the care verifier comes in Phase 2.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from opsdesk.api.session import _serialized
from opsdesk.apps.base import to_json_data
from opsdesk.fullcare import CARE_FILE, office, shiftlife
from opsdesk.fullcare import tools as care_tools
from opsdesk.fullcare.models import CareState
from opsdesk.workforce import tools as wf_tools
from opsdesk.workforce.session import SCHEMA_TEXT, SNAPSHOT_KEYS, WorkforceSession
from opsdesk.workforce.state import AuditEntry

RECORD_KEYS = {"task_id", "steps", "telemetry", "state_hash"}  # the Harbor workspace record: nothing the agent has not seen

ACTIONS = {  # audit-log wording (en, de)
    "care.observation_record": ("Value recorded", "Wert erfasst"),
    "care.fluid_record": ("Fluid recorded", "Ein-/Ausfuhr erfasst"),
    "care.excretion_record": ("Excretion recorded", "Ausscheidung erfasst"),
    "care.assessment_submit": ("Assessment", "Einschätzung"),
    "care.risk_set": ("Risk changed", "Risiko geändert"),
    "care.careplan_entry_add": ("Care-plan entry added", "Planungseintrag angelegt"),
    "care.careplan_entry_update": ("Care-plan entry changed", "Planungseintrag geändert"),
    "care.measure_add": ("Measure added", "Maßnahme angelegt"),
    "care.measure_end": ("Measure ended", "Maßnahme beendet"),
    "care.report_write": ("Care report", "Pflegebericht"),
    "care.order_acknowledge": ("Order acknowledged", "Anordnung quittiert"),
    "care.appointment_create": ("Appointment", "Termin"),
    "care.appointment_cancel": ("Appointment cancelled", "Termin abgesagt"),
    "plan.assign": ("Plan item assigned", "Tagesplan: zugewiesen"),
    "plan.set_status": ("Plan item status", "Tagesplan: Status"),
    "plan.add_item": ("Plan item added", "Tagesplan: ergänzt"),
    "care.admit": ("Admission", "Einzug"), "care.contact_add": ("Contact added", "Kontakt angelegt"),
    "care.allergy_add": ("Allergy recorded", "Allergie erfasst"), "care.status_set": ("Status changed", "Status geändert"),
    "care.wound_create": ("Wound documented", "Wunde angelegt"), "care.wound_assess": ("Wound assessment", "Wundbeurteilung"),
    "care.document_file": ("Document filed", "Dokument abgelegt"), "care.transfer_sheet_save": ("Transfer sheet", "Überleitungsbogen"),
    "print.submit": ("Printed", "Gedruckt"), "print.cancel": ("Print cancelled", "Druck abgebrochen"),
    "care.round_plan": ("Round planned", "Visite geplant"), "care.escalate": ("Note to head of care", "Meldung an die PDL"),
    "care.handover_write": ("Handover written", "Übergabe geschrieben"), "shift.wait": ("Waited", "Gewartet"),
}


def _lf(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    if isinstance(value, dict):
        return {k: _lf(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_lf(v) for v in value]
    return value


def town_of(app) -> str:
    """The town in the home's address line ("Schulstraße 4, 04741 Elsterau" -> "Elsterau")."""
    tail = app.home.address.split(",")[-1].strip()
    return tail.split(" ", 1)[1] if " " in tail and tail.split(" ", 1)[0][:1].isdigit() else tail.split(" ")[0] or "Town"


class CareSession(WorkforceSession):
    read_only_tools = WorkforceSession.read_only_tools | care_tools.CARE_READ_ONLY

    def __init__(self, tasks_root):
        super().__init__(tasks_root)
        self.care: CareState | None = None

    def reset(self, task_id: str, seed: int | None = None) -> dict[str, Any]:
        self.care = None  # Workforce's reset writes the workspace record before the care state exists
        result = super().reset(task_id, seed)
        self.care = self.initial_care()
        shiftlife.sync_clock(self)
        self.write_workspace()
        return {"observation": result["observation"], "tools": self.tool_schemas()}

    def initial_care(self) -> CareState:
        """The bundle's care.json; a plain Workforce bundle gets a care world from its seed and the care address book."""
        assert self.task_dir is not None and self.state is not None and self.app is not None
        path = self.task_dir / CARE_FILE
        if path.exists():
            return CareState.model_validate_json(path.read_text())
        from opsdesk.fullcare.generator import generate  # generation only: not in the buyer image (bundles there have care.json)
        care = generate(self.state, locale=self.app.locale, user_name=self.app.home.user_name, town=town_of(self.app))
        office.prepare_app(care, self.app, town_of(self.app))
        return care

    @_serialized
    def step(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        shiftlife.sync_clock(self)
        args = _lf(args)  # browsers send text-area line breaks as CRLF; stored as LF, so screen and API agree
        result = super().step(name, args)
        if name == "mail.send" and result.get("ok") and self.care is not None:
            office.after_mail(self, result.get("data") or {})
            self.write_workspace()
        if result.get("ok") and name not in self.read_only_tools and self.care is not None and self.care.clock_start is not None:
            shiftlife.after_change(self)  # long tier: arrivals during the shift, colleagues tick off their items
            self.write_workspace()
        self.last_result = result  # host side only (the GUI proof reads what a form did); not part of the state
        if name not in self.read_only_tools:
            self.last_write = (name, result)  # the page shown after a form may read again (browser proof)
        return result

    def care_chat_reply(self, post, channel) -> bool:
        return office.care_chat_reply(self, post, channel)

    # ------------------------------------------------------------------ scoring (host only)
    def is_fullcare_task(self) -> bool:
        return (self.task or {}).get("surface") == "fullcare"

    def care_context(self):
        from opsdesk.fullcare.families import Ctx
        from opsdesk.workforce.state import APP_FILE, WorkforceState
        initial_care = CareState.model_validate_json((self.task_dir / CARE_FILE).read_text())
        initial_app = WorkforceState.model_validate_json((self.task_dir / APP_FILE).read_text())
        return Ctx(world=self.state, initial=initial_care, care=self.care, app=self.app, initial_app=initial_app)

    @_serialized
    def care_score(self) -> dict[str, Any]:
        """Score the workspace as it is now, without closing it (host only)."""
        from opsdesk.fullcare import verifier
        from opsdesk.fullcare.refs import load_reference
        return verifier.score(load_reference(self.tasks_root, self.task["task_id"]), self.care_context())

    @_serialized
    def finish(self) -> dict[str, Any]:
        if not self.is_fullcare_task():
            return super().finish()
        if self.finished is not None:
            return self.finished
        score = self.care_score()
        self.locked = True
        self.cached_score = score
        self.finished = {"score": score, "state_hash": self.state_hash()}
        return self.finished

    def tool_schemas(self, all_tools: bool = False) -> list[dict[str, Any]]:
        """Every tool the workspace offers; `shift.wait` only in the long tier (or with `all_tools`, for a tool list
        shared by every task, e.g. the HUD environment: outside the long tier it then refuses). One entry per name: where
        Workforce lists a 2.4 tool and its own tool of the same name (`messaging.send`, `payroll.export`), only the one
        that runs is listed."""
        long_tier = all_tools or (self.care is not None and self.care.clock_start is not None)
        text = json.dumps([t.definition() for t in care_tools.CARE_TOOLS if long_tier or t.name != "shift.wait"])
        for old, new in SCHEMA_TEXT:
            text = text.replace(old, new)
        base = super().tool_schemas()
        runs = {n: _as_listed(t.definition()) for n, t in wf_tools.WORKFORCE_TOOLS_BY_NAME.items()}  # Workforce runs its own
        base = [t for t in base if t["name"] not in runs or t == runs[t["name"]]]
        return assert_unique_names(sorted(base + json.loads(text), key=lambda t: t["name"]))

    def _invoke_tool(self, name: str, args: dict[str, Any]):
        tool = care_tools.CARE_TOOLS_BY_NAME.get(name)
        if tool is None:
            return super()._invoke_tool(name, args)
        assert self.state is not None and self.app is not None and self.care is not None
        token = care_tools.bind(care_tools.CareContext(self.care, self.app))
        try:
            return tool.invoke(self.state, args)
        finally:
            care_tools.unbind(token)

    def _log(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        if name not in ACTIONS:
            return super()._log(name, args, result)
        pick = 1 if self.app.locale == "de-DE" else 0
        rid = args.get("resident_id")
        if rid is None and self.care is not None:
            key = str(args.get("item_key") or args.get("order_id") or args.get("measure_id") or "")
            rid = next((m.resident_id for m in self.care.measures if key.startswith(m.measure_id)), None) or \
                next((o.resident_id for o in self.care.orders if key.startswith(o.order_id)), None)
        who = self.care.resident(rid).name if self.care and rid and self.care.resident(rid) else ""
        detail = str(args.get("kind") or args.get("catalogue_key") or args.get("status") or args.get("scale_id") or "")
        self.app.session_log.append(AuditEntry(at=self.app.now(self.state.current_date), user=self.app.home.user_name,
                                               action=ACTIONS[name][pick], object=who, before="", after=detail))

    # ------------------------------------------------------------------ state, snapshots, record
    @_serialized
    def state_hash(self) -> str:
        if self.care is None:
            return super().state_hash()
        world = self.state.model_dump(mode="json")
        world.pop("action_log", None)
        payload = {"world": to_json_data(world), "app": to_json_data(self.app.model_dump(mode="json")),
                   "care": to_json_data(self.care.model_dump(mode="json"))}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @_serialized
    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        self.snapshots[-1]["care"] = self.care.model_copy(deep=True)
        return out

    @_serialized
    def restore(self, snapshot_id: int) -> dict[str, Any]:
        snap = self.snapshots[snapshot_id]
        if self.finished is None:
            self.care = snap["care"].model_copy(deep=True)
        out = super().restore(snapshot_id)
        shiftlife.sync_clock(self)
        return out

    def write_workspace(self) -> None:
        """The workspace record (OPSDESK_WORKSPACE_OUT) for Harbor's separate verifier. The agent's container can read
        the folder, so the record holds only what the agent has already seen – the task id, the calls with their results
        (telemetry) and the state hash – never the states themselves (arrivals still to come, colleagues' plans, the
        house-standard status, Workforce's hidden budget). The verifier rebuilds the state by replaying the calls
        (`score_workspace`); `RECORD_KEYS` is checked by the hack audit."""
        folder = os.environ.get("OPSDESK_WORKSPACE_OUT")
        if not folder or self.state is None or self.app is None or getattr(self, "care", None) is None:
            return
        record = {"task_id": self.task["task_id"], "steps": self.steps, "telemetry": self.telemetry_jsonl(),
                  "state_hash": self.state_hash()}
        assert set(record) == RECORD_KEYS
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / ".workspace.json.tmp"
        tmp.write_text(json.dumps(record, sort_keys=True, default=str))
        tmp.replace(out / "workspace.json")

    def export_care(self) -> dict[str, Any]:
        return copy.deepcopy(self.care.model_dump(mode="json"))


def _as_listed(definition: dict[str, Any]) -> dict[str, Any]:
    """A Workforce tool definition as Workforce's list shows it (same wording replacements)."""
    text = json.dumps(definition)
    for old, new in SCHEMA_TEXT:
        text = text.replace(old, new)
    return json.loads(text.replace("include_honesty_summary", "include_summary"))


def assert_unique_names(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A tool list with a name twice is refused (function-calling APIs need unique names); checked at every listing and
    when the server starts."""
    names = [t["name"] for t in tools]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise RuntimeError(f"tool names listed twice: {', '.join(twice)}")
    return tools


assert "steps" in SNAPSHOT_KEYS  # the care snapshot rides on Workforce's snapshot entries
