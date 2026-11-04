"""Score a FullCare workspace record written by the app (`OPSDESK_WORKSPACE_OUT`) – Harbor's separate verifier.

    python -m opsdesk.fullcare.score_workspace --tasks /srv/data/tasks --workspace /workspace-out/workspace.json \
        --out /logs/verifier

As in Workforce (`opsdesk.workforce.score_workspace`): the recorded calls are replayed in a fresh workspace and must
give the recorded final state (a record changed outside the app scores 0); the replayed workspace is then scored by
the FullCare verifier with the reference mounted at `/srv/data/private/fullcare/<task id>.json`. Writes
`reward.json` (`{"reward": <score>, "passed": 1 | 0}` – passed only at exactly 1.0, see `verifier.PASS_RULE`) and
`result.json` (gates, layers, honesty, pass rule, state hash).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def score(tasks_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    from opsdesk.fullcare.session import CareSession
    from opsdesk.workforce.replay import replay_session
    task_id = record["task_id"]
    ws, reproduced = replay_session(tasks_root, task_id, record["telemetry"].splitlines(), session_factory=CareSession)
    if not reproduced or ws.state_hash() != record.get("state_hash"):
        return {"reward": 0.0, "passed": False, "status": "record_does_not_replay", "task_id": task_id}
    from opsdesk.fullcare.licence import finish
    result = finish(ws)  # 0.6.4: without the reference (evaluation licence) a pending 0.0, scored false
    return {"reward": result["score"]["score"], "passed": result["score"]["passed"], "scored": result["score"]["scored"],
            "status": result["status"], "task_id": task_id, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        outcome = score(args.tasks, json.loads(args.workspace.read_text()))
    except (OSError, ValueError, KeyError) as exc:  # no record, or not one the app wrote
        outcome = {"reward": 0.0, "passed": False, "status": "no_valid_workspace_record", "error": type(exc).__name__}
    # reward files hold numbers only (harnesses read every value as a metric): passed as 1 / 0 there, a boolean in result.json
    (args.out / "reward.json").write_text(json.dumps({"reward": outcome["reward"], "passed": int(outcome["passed"]),
                                                      "scored": int(outcome.get("scored", False))}) + "\n")
    (args.out / "result.json").write_text(json.dumps(outcome, indent=2, default=str) + "\n")
    print(json.dumps({"reward": outcome["reward"], "passed": outcome["passed"], "status": outcome["status"]}))


if __name__ == "__main__":
    main()
