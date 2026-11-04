#!/bin/sh
# OpsDesk FullCare 0.6.4 – public sample in one command: build the image, start one workspace, score the empty
# workspace (0), replay the reference solution through the tool API and score again (1.0), clean up.
# Needs Docker, curl and Python 3. Usage: ./quickstart.sh [request id] [port]
set -e
cd "$(dirname "$0")"
TASK="${1:-fc-133cda77edf9}"
PORT="${2:-8080}"
KEY="sample-$$"
IMAGE="${IMAGE:-opsdesk-fullcare-sample:fullcare-0.6.4}"
NAME="opsdesk-fullcare-sample-$PORT"
START=$(date +%s)
docker build -q -t "$IMAGE" docker >/dev/null
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" -e OPSDESK_TASK="$TASK" -e OPSDESK_HOST_KEY="$KEY" \
  -v "$PWD/data/private/fullcare:/srv/data/private/fullcare:ro" "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
n=0; until curl -fs "http://127.0.0.1:$PORT/api/tools" >/dev/null 2>&1; do
  n=$((n + 1)); [ $n -lt 200 ] || { echo "the app did not start"; docker logs "$NAME" | tail -5; exit 1; }; sleep 0.3; done
score() { curl -s -X POST -H "x-opsdesk-host-key: $KEY" "http://127.0.0.1:$PORT/host/finish" \
  | python3 -c "import json,sys; s=json.load(sys.stdin)['score']; print(s['score'], 'passed' if s['passed'] else 'not passed')"; }
echo "request $TASK – the app is at http://127.0.0.1:$PORT while this runs"
echo "empty workspace:    $(score)"
curl -s -X POST -H "x-opsdesk-host-key: $KEY" -H "Content-Type: application/json" -d "{\"task_id\": \"$TASK\"}" \
  "http://127.0.0.1:$PORT/host/reset" >/dev/null
python3 replay_gold.py "formats/harbor/$TASK/solution/calls.json" "http://127.0.0.1:$PORT"
echo "reference replayed: $(score)"
echo "took $(( $(date +%s) - START )) s"
