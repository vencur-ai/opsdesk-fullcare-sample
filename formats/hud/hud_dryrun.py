"""Local HUD dry run: no HUD account, nothing uploaded, no model.

    OPSDESK_CHROMIUM=<chromium binary> python hud_dryrun.py [--out dryrun.json]

Runs the env's initialize hooks, checks the published capabilities (CDP browser + MCP tools), then for
every task in data/tasks: starts the template, replays the recorded gold calls (gold_calls/, public sample
only) through the MCP tools, takes a screenshot over CDP and reads the reward; one more run per task with no
actions must score 0.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "runtime"))


async def main(out: Path) -> None:
    import env as E
    from fastmcp import Client
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # inside the HUD image: no screenshots, the file:// probe still runs
        async_playwright = None
    for hook in E.env._on_start:
        await hook()
    caps = {c.name: c.to_manifest() for c in E.env.capabilities}
    factory = E.env.tasks["workspace"]
    rows = []
    task_ids = sorted(p.name for p in (ROOT / "data" / "tasks").iterdir() if (p / "task.json").is_file())
    pw_cm = async_playwright() if async_playwright else None
    pw = await pw_cm.__aenter__() if pw_cm else None
    file_probe = await _file_probe(E.CDP_PORT, ROOT)
    async with Client(caps["tools"]["url"]) as client:
        tools = await client.list_tools()
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{E.CDP_PORT}") if pw else None
        for task_id in task_ids:
            for mode in ("gold", "noop"):
                gen = factory.func(task_id=task_id)
                prompt = await gen.__anext__()
                refused = 0
                if mode == "gold":
                    for call in json.loads((ROOT / "gold_calls" / f"{task_id}.json").read_text()):
                        result = await client.call_tool(call["tool"].replace(".", "_"), call["args"])
                        refused += not json.loads(result.content[0].text).get("ok", False)
                await asyncio.sleep(0.5)
                pages = [pg for ctx in browser.contexts for pg in ctx.pages if "/care/residents" in pg.url] if browser else []
                shot = None
                if pages:
                    await pages[-1].reload()
                    shot = str(ROOT / f"dryrun_{task_id}_{mode}.png")
                    await pages[-1].screenshot(path=shot)
                frame = await gen.asend(None)
                reward = frame["score"] if isinstance(frame, dict) else frame
                info = frame.get("info", {}) if isinstance(frame, dict) else {}
                rows.append({"task_id": task_id, "mode": mode, "reward": reward, "passed": info.get("passed"),
                             "scored": info.get("scored"), "refused_calls": refused,
                             "prompt_chars": len(prompt), "screenshot": shot})
                print(json.dumps(rows[-1]), flush=True)
    if pw_cm:
        await pw_cm.__aexit__(None, None, None)
    for hook in reversed(E.env._on_stop):
        await hook()
    out.write_text(json.dumps({"capabilities": caps, "tools": len(tools), "file_probe": file_probe, "runs": rows},
                              indent=2) + "\n")


async def _file_probe(port: int, root: Path) -> dict:
    """Open a host-data file in the agent's browser over CDP and report what the page shows."""
    import urllib.request
    import websockets
    target = sorted((root / "data" / "tasks").iterdir())[0] / "task.json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?file://{target}", method="PUT")
    tab = json.loads(urllib.request.urlopen(req, timeout=10).read())
    await asyncio.sleep(1.5)
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": "document.body ? document.body.innerText : ''"}}))
        text = json.loads(await ws.recv()).get("result", {}).get("result", {}).get("value", "")
    # readable = the page shows the file's own lines (not a "blocked" page)
    lines = [line.strip() for line in target.read_text().splitlines() if len(line.strip()) > 8]
    readable = sum(1 for line in lines if line in text) >= min(3, len(lines))
    return {"url": f"file://{target}", "page_text": text[:200], "file_readable": readable}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dryrun.json")
    asyncio.run(main(parser.parse_args().out))
