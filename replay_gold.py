"""Replay a request's reference tool calls against a running OpsDesk FullCare app (standard library only).

    python3 replay_gold.py formats/harbor/<request>/solution/calls.json [http://localhost:8080]
"""
import json
import sys
import urllib.request

url = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8080").rstrip("/")
calls = json.load(open(sys.argv[1]))
for call in calls:
    request = urllib.request.Request(url + "/api/step", data=json.dumps(call).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request).read()
print(f"replayed {len(calls)} calls")
