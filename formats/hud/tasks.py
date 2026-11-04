"""OpsDesk FullCare taskset for HUD: one run per task folder in data/tasks."""

from env import env, workspace

TASK_IDS = [
    "fc-586d61205620",
    "fc-73582e57a857",
    "fc-354cca1ca2d3",
    "fc-a335d975244c",
    "fc-133cda77edf9"
]

tasks = [workspace(task_id=t) for t in TASK_IDS]

__all__ = ["env", "tasks"]
