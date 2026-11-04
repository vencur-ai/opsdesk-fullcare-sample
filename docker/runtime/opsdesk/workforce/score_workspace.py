"""Score a workspace record written by the app (`OPSDESK_WORKSPACE_OUT`) – Harbor's separate verifier (3.1.1).

    python -m opsdesk.workforce.score_workspace --tasks /srv/data/tasks --workspace /workspace-out/workspace.json \
        --out /logs/verifier

The record is written by the app container into a folder the agent's container can only read. The verifier first
replays the recorded calls in a fresh workspace and checks that they give the recorded final state (so a record that
was changed outside the app scores 0), then scores that replayed workspace with the release verifier. Writes
`reward.json` (`{"reward": <total score>}`) and `result.json` (layers, claims, state hash).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def score(tasks_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    from opsdesk.workforce.replay import replay_session
    task_id = record["task_id"]
    ws, reproduced = replay_session(tasks_root, task_id, record["telemetry"].splitlines())
    if not reproduced or ws.state_hash() != record.get("state_hash"):
        return {"reward": 0.0, "status": "record_does_not_replay", "task_id": task_id}
    result = ws.finish()
    return {"reward": result["score"]["total_score"], "status": "scored", "task_id": task_id, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        record = json.loads(args.workspace.read_text())
        outcome = score(args.tasks, record)
    except (OSError, ValueError, KeyError) as exc:  # no record, or not one the app wrote
        outcome = {"reward": 0.0, "status": "no_valid_workspace_record", "error": type(exc).__name__}
    (args.out / "reward.json").write_text(json.dumps({"reward": outcome["reward"]}) + "\n")
    (args.out / "result.json").write_text(json.dumps(outcome, indent=2, default=str) + "\n")
    print(json.dumps({"reward": outcome["reward"], "status": outcome["status"]}))


if __name__ == "__main__":
    main()
