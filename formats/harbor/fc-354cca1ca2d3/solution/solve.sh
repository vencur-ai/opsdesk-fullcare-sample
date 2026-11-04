#!/bin/sh
# Oracle: replays the gold tool calls (recorded host-side from the reference) through the app's tool API.
python - <<'PY'
import json, urllib.request
calls = json.load(open("/solution/calls.json"))
for call in calls:
    req = urllib.request.Request("http://app:8080/api/step", data=json.dumps(call).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
print("replayed", len(calls), "calls")
PY
