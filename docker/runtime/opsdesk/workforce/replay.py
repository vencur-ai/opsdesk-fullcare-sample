"""3.1: replay a workspace's telemetry log and check that it reproduces the final state hash.

    python -m opsdesk.workforce.replay --tasks <tasks root> --task <task id> --log telemetry.jsonl

The log is what `GET /host/telemetry` returns (one JSON object per line): every call with its surface, arguments
and result, plus snapshot and restore events, and a closing `end` record with the state hash. The replay resets
the task, repeats the calls and the snapshot/restore events in order, and compares the hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from opsdesk.workforce.session import WorkforceSession


def replay_session(tasks_root: Path, task_id: str, lines: list[str], session_factory=WorkforceSession) -> tuple[WorkforceSession, bool]:
    """Replay a telemetry log in a fresh workspace; returns the workspace and whether it reproduced the log."""
    result, ws = _replay(tasks_root, task_id, lines, session_factory)
    return ws, result["reproduced"]


def replay(tasks_root: Path, task_id: str, lines: list[str], session_factory=WorkforceSession) -> dict[str, Any]:
    return _replay(tasks_root, task_id, lines, session_factory)[0]


def _replay(tasks_root: Path, task_id: str, lines: list[str], session_factory=WorkforceSession) -> tuple[dict[str, Any], WorkforceSession]:
    ws = session_factory(tasks_root)
    ws.reset(task_id)
    expected, calls, mismatched = None, 0, []
    for line in lines:
        if not line.strip():
            continue
        item = json.loads(line)
        if item["event"] == "call":
            result = ws.step(item["tool"], item["args"])
            calls += 1
            if bool(result.get("ok")) != item["ok"]:
                mismatched.append(item["seq"])
        elif item["event"] == "snapshot":
            ws.snapshot()
        elif item["event"] == "restore":
            if ws.restore(int(item["snapshot_id"]))["state_hash"] != item["state_hash"]:
                mismatched.append(item["seq"])
        elif item["event"] == "end":
            expected = item["state_hash"]
    final = ws.state_hash()
    return ({"task_id": task_id, "calls": calls, "state_hash": final, "expected": expected,
             "reproduced": expected == final and not mismatched, "mismatched_seq": mismatched}, ws)


def prove(tasks_root: Path, task_ids: list[str]) -> list[dict[str, Any]]:
    """Release proof: GUI forms (gold), a snapshot, a detour through the API, a restore, the rest of the work,
    then the exported telemetry is replayed in a fresh workspace and must reproduce the final hash."""
    import os
    import secrets
    from fastapi.testclient import TestClient
    from opsdesk.workforce.agents import gold_gui
    from opsdesk.workforce.server import create_workforce_app
    key = os.environ.setdefault("OPSDESK_HOST_KEY", secrets.token_hex(16))
    host = {"x-opsdesk-host-key": key}
    rows = []
    for task_id in task_ids:
        app = create_workforce_app(tasks_root, task_id)
        ws = app.state.ws
        with TestClient(app) as client:
            client.get("/rota")
            snap = client.post("/host/snapshot", headers=host).json()
            client.post("/api/step", json={"tool": "chat.post", "args": {"channel_id": "chat-all", "text": "…"}})
            client.post("/host/restore", json={"snapshot_id": snap["snapshot_id"]}, headers=host)
            gold_gui(client, ws, tasks_root / task_id)
            log = client.get("/host/telemetry", headers=host).text
        events = [json.loads(line) for line in log.splitlines()]
        result = replay(tasks_root, task_id, log.splitlines())
        rows.append({**result, "events": len(events), "sources": sorted({e.get("source") for e in events if e["event"] == "call"})})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--task")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--prove", type=int, default=0, help="release proof on this many requests (spread over the set)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.prove:
        ids = sorted(p.name for p in args.tasks.iterdir() if (p / "task.json").is_file())
        step = max(1, len(ids) // args.prove)
        rows = prove(args.tasks, ids[::step][: args.prove])
        if args.out:
            args.out.write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps({"reproduced": sum(r["reproduced"] for r in rows), "of": len(rows)}))
        sys.exit(0 if all(r["reproduced"] for r in rows) else 1)
    result = replay(args.tasks, args.task, args.log.read_text().splitlines())
    print(json.dumps(result))
    sys.exit(0 if result["reproduced"] else 1)


if __name__ == "__main__":
    main()
