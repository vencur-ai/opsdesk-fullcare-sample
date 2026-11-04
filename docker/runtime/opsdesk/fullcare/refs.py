"""Where a FullCare task's reference lives: `<tasks root>/../private/fullcare/<task id>.json` (host side only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PRIVATE = Path("private") / "fullcare"


def reference_path(tasks_root: Path, task_id: str) -> Path:
    return Path(tasks_root).parent / PRIVATE / f"{task_id}.json"


def load_reference(tasks_root: Path, task_id: str) -> dict[str, Any]:
    return json.loads(reference_path(tasks_root, task_id).read_text())
