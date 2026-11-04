#!/bin/sh
# Verifier (separate container built from tests/; the agent never sees it): replay the workspace record the app
# wrote, check that it gives the recorded state, score it. Writes /logs/verifier/reward.json and result.json.
set -e
mkdir -p /logs/verifier
python -m opsdesk.fullcare.score_workspace --tasks /srv/data/tasks --workspace /workspace-out/workspace.json --out /logs/verifier
