"""``rootpilot serve``: a loopback-only, token-gated control page for the phone (SPEC §3.10).

A home-screen web-clip opens this page (see ``agent/webclip.py``). From it you start a task,
watch each step live, answer the confirm prompts, and stop a run. The server:

- binds strictly to a loopback address (refuses anything else, like the ZXTouch backend);
- serves every route under a secret ``/<token>/`` prefix; a wrong token is a 404;
- runs at most one task at a time, on a background thread.

It is off by default. Starting it is the only way RootPilot opens a listening socket, and even
then only on loopback — nothing here is reachable from the network.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from agent.audit import recent_runs
from agent.config import Config
from agent.errors import ConfigError
from agent.loop import ALWAYS, NO, YES, Agent, RunResult
from agent.paths import (
    ensure_private_dir,
    hud_status_file,
    serve_token_file,
    state_dir,
    stop_file,
)
from agent.permissions import Decision, Rule
from agent.tools.registry import Category

CONFIRM_TIMEOUT = 180.0
MAX_LINES = 120
CONTROL_HTML = (Path(__file__).resolve().parent / "webui" / "control.html").read_text("utf-8")

AgentBuilder = Callable[["ControlState", str], Agent]


@dataclass
class Pending:
    id: str
    kind: str  # "confirm" | "ask"
    tool: str
    reason: str
    allow_always: bool
    event: threading.Event = field(default_factory=threading.Event)
    answer: Optional[str] = None


@dataclass
class ControlState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    run_id: str = ""
    task: str = ""
    lines: list[str] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    pending: Optional[Pending] = None

    def report(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)
            del self.lines[:-MAX_LINES]

    def begin(self, run_id: str, task: str) -> None:
        with self.lock:
            self.running, self.run_id, self.task = True, run_id, task
            self.lines, self.result, self.pending = [], None, None

    def finish(self, result: RunResult) -> None:
        with self.lock:
            self.running = False
            self.pending = None
            self.result = {
                "answer": result.answer,
                "success": result.success,
                "reason": result.reason,
                "steps": result.steps,
            }

    def ask(self, pending: Pending) -> str:
        with self.lock:
            self.pending = pending
        ok = pending.event.wait(CONFIRM_TIMEOUT)
        with self.lock:
            self.pending = None
            return pending.answer if (ok and pending.answer is not None) else ""

    def answer(self, pid: str, value: str) -> bool:
        with self.lock:
            p = self.pending
            if p is None or p.id != pid:
                return False
            p.answer = value
            p.event.set()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            pending = None
            if self.pending is not None:
                pending = {
                    "id": self.pending.id,
                    "kind": self.pending.kind,
                    "tool": self.pending.tool,
                    "reason": self.pending.reason,
                    "allow_always": self.pending.allow_always,
                }
            return {
                "running": self.running,
                "run_id": self.run_id,
                "task": self.task,
                "lines": list(self.lines),
                "result": self.result,
                "pending": pending,
            }


def server_confirm(state: ControlState) -> Callable[..., str]:
    def confirm(tool: Any, args: dict[str, Any], decision: Decision) -> str:
        allow_always = decision.rule is Rule.DEFAULT and tool.category is not Category.SHELL
        p = Pending(secrets.token_hex(6), "confirm", tool.name, decision.reason, allow_always)
        answer = state.ask(p)
        return {"yes": YES, "always": ALWAYS}.get(answer, NO)

    return confirm


def server_ask(state: ControlState) -> Callable[[str], str]:
    def ask(question: str) -> str:
        return state.ask(Pending(secrets.token_hex(6), "ask", "ask_user", question, False))

    return ask


def default_build_agent(state: ControlState, task: str) -> Agent:
    # Imported lazily so `serve` starts even when a provider/backend is misconfigured.
    from agent.cli import make_agent_for_serve

    return make_agent_for_serve(state)


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


class ControlServer:
    def __init__(
        self,
        cfg: Config,
        build_agent: AgentBuilder = default_build_agent,
        token: Optional[str] = None,
    ) -> None:
        self.cfg = cfg
        self.state = ControlState()
        self._build = build_agent
        self.token = token or _load_token()

    def start_run(self, task: str) -> bool:
        with self.state.lock:
            if self.state.running:
                return False
            self.state.running = True
        thread = threading.Thread(target=self._run, args=(task,), daemon=True)
        thread.start()
        return True

    def _run(self, task: str) -> None:
        stop_file().unlink(missing_ok=True)  # clear a STOP left by a previous run
        agent = self._build(self.state, task)
        self.state.begin(agent.run_id, task)
        try:
            result = agent.run(task)
        finally:
            agent.audit.close()
            agent.backend.close()
        self.state.finish(result)

    def stop_run(self) -> None:
        ensure_private_dir(state_dir())
        stop_file().touch()
        # Release a waiting confirmation so the run can end promptly.
        if self.state.pending is not None:
            self.state.answer(self.state.pending.id, "")

    def serve(self, host: str = "127.0.0.1") -> ThreadingHTTPServer:
        if not _loopback(host):
            raise ConfigError(f"refusing to bind {host!r}: the control server is loopback-only")
        httpd = ThreadingHTTPServer((host, self.cfg.serve_port), _make_handler(self))
        return httpd

    @property
    def base(self) -> str:
        return f"/{self.token}"

    def info(self) -> dict[str, Any]:
        """What the page's status strip shows. Never includes keys or other secrets."""
        from agent import __version__
        from agent.config import load_config
        from agent.indicator import overlay_status

        try:
            cfg = load_config()
            model = cfg.model or {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-flash"}.get(
                cfg.provider, ""
            )
            provider, backend, error = cfg.provider, cfg.backend, ""
        except ConfigError as exc:
            provider = model = backend = ""
            error = str(exc)
        zxtouch: Optional[bool] = None
        if backend == "zxtouch":
            from agent.backends.zxtouch import is_exposed, listening_addresses

            addrs = listening_addresses(self.cfg.zxtouch_port)
            zxtouch = None if addrs is None else (bool(addrs) and not is_exposed(addrs))
        return {
            "version": __version__,
            "provider": provider,
            "model": model,
            "backend": backend,
            "config_error": error,
            "overlay": overlay_status(hud_status_file()),
            "zxtouch_loopback_only": zxtouch,
        }


def _load_token() -> str:
    path = serve_token_file()
    try:
        token = path.read_text("utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    ensure_private_dir(path.parent)
    fd_path = path.with_name(path.name + ".tmp")
    fd_path.write_text(token, encoding="utf-8")
    fd_path.replace(path)
    return token


def _make_handler(server: ControlServer) -> type[BaseHTTPRequestHandler]:
    base = server.base

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # keep the server quiet
            return

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _guard(self) -> Optional[str]:
            # Loopback Host header (defeats DNS-rebinding) and the secret path prefix.
            host = self.headers.get("Host", "").split(":")[0]
            if host and not _loopback(host):
                self._send(421, b"bad host", "text/plain")
                return None
            if not self.path.startswith(base + "/") and self.path != base:
                self._send(404, b"not found", "text/plain")
                return None
            return self.path[len(base) :] or "/"

        def do_GET(self) -> None:
            route = self._guard()
            if route is None:
                return
            if route == "/":
                html = CONTROL_HTML.replace("__BASE__", base)
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif route == "/api/status":
                self._json(200, server.state.snapshot())
            elif route == "/api/info":
                self._json(200, server.info())
            elif route == "/api/runs":
                self._json(200, recent_runs(state_dir() / "runs"))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            route = self._guard()
            if route is None:
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            if route == "/api/run":
                task = str(body.get("task", "")).strip()
                if not task:
                    self._json(400, {"error": "empty task"})
                elif not server.start_run(task):
                    self._json(409, {"error": "a run is already in progress"})
                else:
                    self._json(200, {"ok": True})
            elif route == "/api/stop":
                server.stop_run()
                self._json(200, {"ok": True})
            elif route == "/api/confirm":
                ok = server.state.answer(str(body.get("id", "")), str(body.get("answer", "")))
                self._json(200 if ok else 409, {"ok": ok})
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def run_server(cfg: Config, out: Any, host: str = "127.0.0.1") -> int:
    server = ControlServer(cfg)
    httpd = server.serve(host)
    url = f"http://{host}:{cfg.serve_port}{server.base}/"
    out.write(f"RootPilot control server on {url}\n(loopback only; Ctrl-C to stop)\n")
    out.write("Install the home-screen icon with:  rootpilot install-icon\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        out.write("\nstopped\n")
    finally:
        httpd.server_close()
    return 0
