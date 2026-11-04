# Plain HTTP

Run one workspace: `docker run -p 8080:8080 -e OPSDESK_TASK=<task id> -e OPSDESK_HOST_KEY=<secret> opsdesk-fullcare-sample:fullcare-0.6.4`.

| Who | Route | What |
|---|---|---|
| Computer-use agent | `GET /` (browser) | the OpsDesk FullCare app (care record, daily plan, rota, mail, chat, calendar), 1280×800 or larger |
| Tool-use agent | `GET /api/tools` | tool schemas (the same functions as the screens) |
| Tool-use agent | `POST /api/step` | `{"tool": "care.observation_record", "args": {...}}` |
| Tool-use agent | `GET /api/state` | the visible state |
| Host | `POST /host/reset` | `{"task_id": ...}` – fresh workspace (`x-opsdesk-host-key`) |
| Host | `POST /host/finish` | deterministic score (the reference must be mounted at `/srv/data/private/fullcare`) |
| Host | `GET /host/care-state`, `GET /host/state-hash`, `GET /host/export` | care record, state hash, full export |
