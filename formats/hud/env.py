"""OpsDesk FullCare as a HUD environment: computer-use plus MCP on one workspace.

The app (rota, staff, absences, requests, messages, agency, publishing, understaffing reports, audit
log) runs in this container. A headless Chromium shows it to computer-use agents over CDP; the same
domain functions are offered as MCP tools. The reward is the deterministic OpsDesk verifier score
(solver references, hard gates, collateral-damage check, strict honesty from in-app actions).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "runtime"))

from hud import Environment  # noqa: E402
from hud.capabilities import Capability  # noqa: E402

env = Environment(name="opsdesk-fullcare")
TASKS_ROOT = Path(os.environ.get("OPSDESK_TASKS_ROOT", ROOT / "data" / "tasks"))
APP_PORT = int(os.environ.get("OPSDESK_APP_PORT", "8080"))
CDP_PORT = int(os.environ.get("OPSDESK_CDP_PORT", "9222"))
CHROMIUM = os.environ.get("OPSDESK_CHROMIUM", "chromium")
_state: dict[str, Any] = {"app": None, "server": None, "browser": None, "mcp": None, "mcp_port": 0}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def _start_app() -> Any:
    import uvicorn
    from opsdesk.fullcare.server import create_fullcare_app
    app = create_fullcare_app(TASKS_ROOT)  # empty workspace; each task resets it
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=APP_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait(APP_PORT)
    _state.update(app=app, server=server)
    return app


def _start_browser(lang: str = "en-GB") -> None:
    """Headless Chromium in the home's language (`--lang`: date and time fields as a UK or German user sees them)."""
    profile = Path(os.environ.get("OPSDESK_CHROMIUM_PROFILE", "/tmp/opsdesk-chromium"))
    profile.mkdir(parents=True, exist_ok=True)
    _state["browser"] = subprocess.Popen([
        CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}", "--window-size=1280,800", f"--lang={lang}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _state["lang"] = lang
    _wait(CDP_PORT)


def _open_app_tab() -> None:
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/new?http://127.0.0.1:{APP_PORT}/care/residents", method="PUT")
    urllib.request.urlopen(req, timeout=10).read()


def _tools() -> list[Any]:
    from fastmcp.tools import Tool, ToolResult
    from mcp.types import TextContent
    from opsdesk.fullcare.session import CareSession

    class WorkspaceTool(Tool):
        workspace_name: str

        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            ws = _state["app"].state.ws
            with _state["app"].state.lock:
                result = ws.step(self.workspace_name, arguments or {})
            return ToolResult(content=[TextContent(type="text", text=json.dumps(result, default=str))])

    schemas = CareSession(TASKS_ROOT).tool_schemas(all_tools=True)  # the same list for every task
    return [WorkspaceTool(name=s["name"].replace(".", "_"), description=s.get("description", ""),
                          parameters=dict(s.get("parameters") or {"type": "object", "properties": {}}),
                          workspace_name=s["name"]) for s in schemas]


@env.initialize
async def _up() -> None:
    """Start the app, a headless Chromium on it (CDP) and the MCP tools, and publish both capabilities."""
    from fastmcp import FastMCP
    if _state["app"] is None:
        _start_app()
    if _state["browser"] is None:
        _start_browser()
    if _state["mcp"] is None:
        server = FastMCP(name="opsdesk-fullcare")
        for tool in _tools():
            server.add_tool(tool)
        _state["mcp_port"] = _free_port()
        _state["mcp"] = asyncio.create_task(server.run_async(transport="http", host="127.0.0.1", port=_state["mcp_port"],
                                                             show_banner=False))
        await asyncio.to_thread(_wait, _state["mcp_port"])
    env.add_capability(Capability.cdp(name="browser", url=f"ws://127.0.0.1:{CDP_PORT}"))
    env.add_capability(Capability.mcp(name="tools", url=f"http://127.0.0.1:{_state['mcp_port']}/mcp"))


@env.shutdown
async def _down() -> None:
    if _state.get("browser") is not None:
        _state["browser"].terminate()
    if _state.get("server") is not None:
        _state["server"].should_exit = True
    if _state.get("mcp") is not None:
        _state["mcp"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _state["mcp"]
    _state.update(app=None, server=None, browser=None, mcp=None)


@env.template(id="workspace", description="One OpsDesk FullCare request, done in the app (browser and/or MCP tools).")
async def workspace(task_id: str) -> AsyncGenerator[Any, str | None]:
    """Open the workspace for one request and score the result with the deterministic verifier.

    Args:
        task_id: Folder name under data/tasks.
    """
    app = _state["app"]
    with app.state.lock:
        app.state.ws.reset(task_id)
        app.state.flash = None
    ws = app.state.ws
    if _state.get("lang") != ws.app.locale and _state.get("browser") is not None:  # a home in the other country
        _state["browser"].terminate()
        _state["browser"].wait(timeout=10)
        _start_browser(ws.app.locale)
    _open_app_tab()
    access = ("OpsDesk FullCare is open in the browser. The same functions are available as tools."
              if ws.app.locale == "en-GB" else
              "OpsDesk FullCare ist im Browser geöffnet. Dieselben Funktionen gibt es auch als Werkzeuge.")
    yield f"{ws.task['instruction']}\n\n{access}"
    with app.state.lock:
        from opsdesk.fullcare.licence import finish, hud_frame
        result = finish(ws)
    yield hud_frame(result)  # numeric score; passed and scored in info (0.6.4)
