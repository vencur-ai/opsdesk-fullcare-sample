# Quickstart – OpsDesk FullCare sample

You need Docker (running), curl and Python 3. Nothing else is installed on your machine; the app runs offline in one
container per workspace.

## 1. One command

```bash
./quickstart.sh                    # default request fc-133cda77edf9, port 8080
./quickstart.sh <request> [port]   # any of the five
```

It prints the score of the empty workspace (0, not passed) and of the replayed reference solution (1.0, passed).

The five requests:

- `fc-586d61205620` – Early shift (long tier) (DE)
- `fc-73582e57a857` – Admission (UK)
- `fc-354cca1ca2d3` – GP orders (DE)
- `fc-a335d975244c` – Hospital transfer (DE)
- `fc-133cda77edf9` – Hospital return (UK)

## 2. By hand: start a workspace

```bash
docker build -t opsdesk-fullcare-sample:fullcare-0.6.4 docker
docker run -d --name opsdesk -p 8080:8080 -e OPSDESK_TASK=fc-133cda77edf9 -e OPSDESK_HOST_KEY=change-me \
  -v "$PWD/data/private/fullcare:/srv/data/private/fullcare:ro" opsdesk-fullcare-sample:fullcare-0.6.4
```

Open http://localhost:8080 – that is the screen a computer-use agent sees (1280×800 or larger; German requests use
the German edition). The request text is in `data/tasks/<request>/task.json` → `instruction` (and in
`formats/harbor/<request>/instruction.md`).

## 3. Connect your agent

- **Computer use:** give the agent the request text and a browser on http://localhost:8080.
- **Tools over HTTP:** `GET /api/tools` lists the tool schemas; `POST /api/step` with
  `{"tool": "care.residents", "args": {"status": "all", "limit": 20}}` calls one. Mail, chat, calendar, rota and
  printing have their own tools; colleagues answer in the same step. In the early shift (`shift.wait`) mail and chat
  arrive on the clock.
- **MCP (stdio):** `python -m opsdesk.fullcare.mcp --server http://localhost:8080`, run in `docker/runtime` after
  `pip install --require-hashes -r requirements.lock`.
- **Harbor / HUD / OpenEnv:** `formats/`. Harbor: each folder under `formats/harbor/` is a task; the app writes its
  workspace record into a volume the agent's container can only read, and a separate verifier container built from
  `tests/` scores it. Build the image once first: `docker build -t opsdesk-fullcare-sample:fullcare-0.6.4 docker`.

## 4. Score

```bash
curl -s -X POST -H "x-opsdesk-host-key: change-me" localhost:8080/host/finish
```

The answer holds `score.score` (0–1), `score.passed` (true only at exactly 1.0) and every gate and check with its
details. Next run:

```bash
curl -s -X POST -H "x-opsdesk-host-key: change-me" -H "Content-Type: application/json" \
  -d '{"task_id": "fc-133cda77edf9"}' localhost:8080/host/reset
docker rm -f opsdesk                # when you are done
```

The host key protects `/host/*`; the agent never needs it.
