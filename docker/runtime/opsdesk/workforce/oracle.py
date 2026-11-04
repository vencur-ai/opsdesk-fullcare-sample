"""Oracle for Harbor `solution/solve.sh`: replays recorded gold tool calls against a running workspace.

    python -m opsdesk.workforce.oracle --url http://127.0.0.1:8080 --calls /solution/calls.json

The calls are recorded host-side by running the gold agent on a fresh workspace of the same task
(IDs are deterministic, so the replay reaches the same state). Only shipped where the licence
includes references (public sample).
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def record_gold_calls(tasks_root: Path, task_id: str) -> list[dict]:
    from opsdesk.workforce.agents import gold_api
    from opsdesk.workforce.session import WorkforceSession
    ws = WorkforceSession(tasks_root)
    ws.reset(task_id)
    gold_api(ws, tasks_root / task_id)
    return [{"tool": tool, "args": args} for tool, args, _ok in ws.call_log]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--calls", type=Path, required=True)
    args = parser.parse_args()
    failed = 0
    for call in json.loads(args.calls.read_text()):
        req = urllib.request.Request(args.url.rstrip("/") + "/api/step", data=json.dumps(call).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        result = json.load(urllib.request.urlopen(req))
        failed += not result.get("ok", False)
    print(f"replayed calls, {failed} refused")


if __name__ == "__main__":
    main()
