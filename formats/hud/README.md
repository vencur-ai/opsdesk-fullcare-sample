# HUD environment (computer-use + MCP) – not deployed

- `env.py`: one template `workspace(task_id)`; starts the OpsDesk FullCare app, a headless Chromium on the app
  (capability `browser`, CDP) and the same functions as MCP tools (capability `tools`). Reward = deterministic
  verifier score at the end (claims from in-app actions, strict honesty).
- Build context: this folder plus `runtime/` (the `opsdesk` package of release 3.1.1) and `data/`
  (tasks, host data and the host references). `data/` is assembled at listing time with
  `python -m opsdesk.fullcare.export --candidate <set> --out <dir> --with-host-references`; references stay inside
  the container, the agent only sees the browser and the tools.
- Local dry run with the HUD SDK, no account, nothing uploaded: `OPSDESK_CHROMIUM=<chromium> python hud_dryrun.py`
  (needs `data/private/references` for the gold replay).
