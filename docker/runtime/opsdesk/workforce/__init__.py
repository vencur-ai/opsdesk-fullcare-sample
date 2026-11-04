"""OpsDesk Workforce (3.0): the care-home workforce-management replica on the OpsDesk domain core.

GUI (server-rendered HTML) and tool API share one `WorkforceSession`; every change goes through
`EpisodeSession.step`. Claims for the verifier come from in-app actions (`claims.py`).
"""

SURFACE = "workforce"


def is_workforce_task(task: dict) -> bool:
    return task.get("surface") == SURFACE
