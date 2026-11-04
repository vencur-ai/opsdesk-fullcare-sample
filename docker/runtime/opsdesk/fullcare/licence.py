"""0.6.4: what a FullCare delivery's own scorer returns, per licence (as Workforce 3.2, `opsdesk.workforce.licence`).

- With the request's reference (training licence: the sealed verifier image; the public sample): the verifier's
  result, unchanged, plus `scored: true`.
- Without it (evaluation licence: the references stay with Vencur): a pending score – 0.0, `passed: false`,
  `scored: false`, status `pending_provider_scoring` – and the workspace is locked as `finish()` would lock it. The
  workspace record (`OPSDESK_WORKSPACE_OUT`) is kept; Vencur scores the returned records. The pending 0.0 is never a
  result. Before 0.6.4 the HTTP finish answered 500 and the HUD template raised at grading.

`finish(ws)` is the one place the HTTP host route, the HUD template and `score_workspace` ask for the end-of-run
outcome; `CareSession.finish()` and the verifier are unchanged.
"""

from __future__ import annotations

from typing import Any

PENDING = "pending_provider_scoring"
PENDING_NOTE = ("Evaluation licence: the references stay with the provider, so this run is not scored here. "
                "0.0 is not a result; Vencur scores the kept workspace record.")


def has_reference(ws: Any) -> bool:
    from opsdesk.fullcare.refs import reference_path
    return reference_path(ws.tasks_root, ws.task["task_id"]).is_file()


def finish(ws: Any) -> dict[str, Any]:
    if not ws.is_fullcare_task() or has_reference(ws):
        result = ws.finish()
        return {**result, "score": {**result["score"], "scored": True}, "status": "scored"}
    ws.locked = True
    return {"score": {"task_id": ws.task["task_id"], "score": 0.0, "passed": False, "scored": False},
            "status": PENDING, "note": PENDING_NOTE, "state_hash": ws.state_hash()}


def hud_frame(result: dict[str, Any]) -> dict[str, Any]:
    """HUD's grade frame: a numeric `score`, and `passed`/`scored` in `info`."""
    score = result["score"]
    frame = {"score": float(score["score"]), "done": True,
             "info": {"passed": bool(score["passed"]), "scored": bool(score["scored"]), "status": result["status"]}}
    if not score["scored"]:
        frame["content"] = PENDING_NOTE
    return frame
