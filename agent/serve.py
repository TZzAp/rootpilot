"""``rootpilot serve``: a loopback-only, token-gated control page for the phone (SPEC §3.10).

A home-screen web-clip opens this page (see ``agent/webclip.py``). From it you start a task,
watch each step live, answer the confirm prompts, and stop a run. The server:

- binds strictly to a loopback address (refuses anything else, like the ZXTouch backend);
- serves every route under a secret ``/<token>/`` prefix; a wrong token is a 404;
- runs at most one task at a time, on a background thread.

It is off by default. Starting it is the only way RootPilot opens a listening socket, and even
then only on loopback — nothing here is reachable from the network.

It also serves the RootPilot Arena (agent/arena.py): seeded mini-games under ``/arena/`` that
the benchmark runner has the agent play. Arena pages carry no control token (the agent's task
URL must not leak it) and can only report scores for sessions the benchmark created.
"""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from agent import control_api
from agent.arena import GAMES, SUITES, ArenaSessions, score_game, task_for, totals
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
from agent.types import Usage

CONFIRM_TIMEOUT = 180.0
MAX_LINES = 120
WEBUI = Path(__file__).resolve().parent / "webui"
CONTROL_HTML = (WEBUI / "control.html").read_text("utf-8")
ARENA_HTML = (WEBUI / "arena.html").read_text("utf-8")
DEVICE_NAMES = {
    "iPhone9,1": "iPhone 7", "iPhone9,3": "iPhone 7", "iPhone9,2": "iPhone 7 Plus",
    "iPhone9,4": "iPhone 7 Plus", "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus", "iPhone10,3": "iPhone X",
    "iPhone10,6": "iPhone X",
}  # fmt: skip

# (state, task) or (state, task, overrides=...) -> Agent
AgentBuilder = Callable[..., Agent]


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


def default_build_agent(
    state: ControlState,
    task: str,
    overrides: Optional[dict[str, Any]] = None,
    bench: bool = False,
) -> Agent:
    # Imported lazily so `serve` starts even when a provider/backend is misconfigured.
    from agent.cli import make_agent_for_serve

    return make_agent_for_serve(state, overrides, bench)


def device_info() -> dict[str, str]:
    from agent.paths import jbroot

    machine = platform.machine()
    ios = ""
    sw_vers = shutil.which("sw_vers") or str(jbroot() / "usr" / "bin" / "sw_vers")
    try:
        ios = subprocess.run(
            [sw_vers, "-productVersion"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "machine": machine,
        "name": DEVICE_NAMES.get(machine, machine),
        "ios": ios,
        "jailbreak": "rootless" if str(jbroot()) != "/" else "rootful",
    }


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        # Safari drops idle keep-alive connections all the time; Python 3.9 would print a
        # full traceback for each one into serve.log.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


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
        self.started = time.time()  # lets an open page notice a restart and reload itself
        self.arena = ArenaSessions()
        self.bench: dict[str, Any] = {"running": False}
        self._bench_abort = False

    def start_run(self, task: str) -> bool:
        with self.state.lock:
            if self.state.running or self.bench.get("running"):
                return False
            self.state.running = True
        thread = threading.Thread(target=self._run, args=(task,), daemon=True)
        thread.start()
        return True

    def _run(self, task: str) -> None:
        self._execute(task)

    def _execute(
        self, task: str, overrides: Optional[dict[str, Any]] = None, bench: bool = False
    ) -> RunResult:
        """Run one task to the end, keeping the page's state current. Never raises."""
        stop_file().unlink(missing_ok=True)  # clear a STOP left by a previous run
        try:
            extra: dict[str, Any] = {"overrides": overrides} if overrides else {}
            if bench:
                extra["bench"] = True
            agent = self._build(self.state, task, **extra)
        except Exception as exc:
            self.state.begin("", task)
            self.state.report(f"⚠️ cannot start: {exc}")
            failed = RunResult("", f"Cannot start: {exc}", False, "error", 0, Usage())
            self.state.finish(failed)
            return failed
        self.state.begin(agent.run_id, task)
        result: Optional[RunResult] = None
        try:
            result = agent.run(task)
        except Exception as exc:
            self.state.report(f"⚠️ run crashed: {exc}")
            result = RunResult(agent.run_id, f"Run crashed: {exc}", False, "error", 0, Usage())
        finally:
            agent.audit.close()
            agent.backend.close()
            ended = RunResult(agent.run_id, "Run ended", False, "error", 0, Usage())
            self.state.finish(result or ended)
        return result or ended

    # ---- benchmark --------------------------------------------------------------------

    def start_bench(self, suite: str, seed: int) -> Optional[str]:
        """Start a suite in the background; returns its report id, or None if busy."""
        if suite not in SUITES:
            raise ConfigError(f"unknown suite {suite!r}; choose {', '.join(SUITES)}")
        bench_id = time.strftime("%Y%m%d-%H%M%S")
        with self.state.lock:
            if self.state.running or self.bench.get("running"):
                return None
            self.bench = {
                "running": True,
                "id": bench_id,
                "suite": suite,
                "seed": seed,
                "rows": [],
                "current": "",
            }
        threading.Thread(target=self._bench, args=(suite, seed, bench_id), daemon=True).start()
        return bench_id

    def _save_report(self, report: dict[str, Any]) -> None:
        # Saved after every game, so a crash (SpringBoard, the phone, this server) loses at most
        # the game in progress.
        report["totals"] = totals(report["games"])
        folder = ensure_private_dir(state_dir() / "bench")
        path = folder / f"{report['id']}.json"
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        os.replace(tmp, path)

    def _bench(self, suite: str, seed: int, bench_id: str) -> None:
        from agent.config import load_config
        from agent.indicator import hud_installed

        self._bench_abort = False
        try:
            cfg = load_config()
            settings = {
                "provider": cfg.provider,
                "model": cfg.model,
                "effort": cfg.effort or "default",
                "screenshots": cfg.screenshots,
            }
            ocr = cfg.ocr
        except ConfigError as exc:
            settings, ocr = {"error": str(exc)}, "auto"
        if settings.get("provider") and not settings.get("model"):
            from agent.providers import DEFAULT_MODELS

            settings["model"] = DEFAULT_MODELS.get(str(settings["provider"]), "")
        settings["ocr"] = (
            "overlay" if ocr == "hud" or (ocr == "auto" and hud_installed()) else "zxtouch"
        )
        report: dict[str, Any] = {
            "id": bench_id,
            "suite": suite,
            "seed": seed,
            "started": time.time(),
            "device": device_info(),
            "settings": settings,
            "games": [],
        }
        try:
            for gid in SUITES[suite]:
                if self._bench_abort:
                    break
                game = GAMES[gid]
                sid = self.arena.create(gid)
                url = (
                    f"http://127.0.0.1:{self.cfg.serve_port}/arena/{gid}"
                    f"?seed={seed}&s={sid}&r={game.rounds}"
                )
                self.bench["current"] = gid
                began = time.monotonic()
                result = self._execute(
                    task_for(game, url, seed),
                    {"max_steps": game.max_steps, "max_seconds": game.max_seconds},
                    bench=True,
                )
                time.sleep(1.0)  # let the page's last score report land
                row = score_game(
                    game, seed, self.arena.result(sid), result, time.monotonic() - began
                )
                row["run_id"] = result.run_id
                report["games"].append(row)
                self.bench["rows"] = list(report["games"])
                report["aborted"] = False
                self._save_report(report)
                if result.reason == "stopped":
                    self._bench_abort = True
        finally:
            report["finished"] = time.time()
            report["aborted"] = self._bench_abort
            self._save_report(report)
            self.bench = {"running": False, "last": report}

    def bench_status(self) -> dict[str, Any]:
        reports = []
        for path in sorted((state_dir() / "bench").glob("*.json"), reverse=True)[:12]:
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reports.append(
                {k: r.get(k) for k in ("id", "suite", "seed", "settings", "totals", "aborted")}
            )
        return {**self.bench, "suites": {k: list(v) for k, v in SUITES.items()}, "reports": reports}

    def bench_report(self, bench_id: str) -> Optional[dict[str, Any]]:
        if not control_api.RUN_ID.match(bench_id):
            return None
        path = state_dir() / "bench" / f"{bench_id}.json"
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def stop_run(self) -> None:
        self._bench_abort = True
        ensure_private_dir(state_dir())
        stop_file().touch()
        # Release a waiting confirmation so the run can end promptly.
        if self.state.pending is not None:
            self.state.answer(self.state.pending.id, "")

    def serve(self, host: str = "127.0.0.1") -> ThreadingHTTPServer:
        if not _loopback(host):
            raise ConfigError(f"refusing to bind {host!r}: the control server is loopback-only")
        httpd = _QuietServer((host, self.cfg.serve_port), _make_handler(self))
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
        effort = ""
        try:
            effort = load_config().effort
        except ConfigError:
            pass
        return {
            "started": self.started,
            "version": __version__,
            "provider": provider,
            "model": model,
            "effort": effort or "default",
            "bench_running": bool(self.bench.get("running")),
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
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _host_ok(self) -> bool:
            # Loopback Host header defeats DNS rebinding from a web page.
            host = self.headers.get("Host", "").split(":")[0]
            if host and not _loopback(host):
                self._send(421, b"bad host", "text/plain")
                return False
            return True

        def _body(self) -> dict[str, Any]:
            length = min(int(self.headers.get("Content-Length", "0") or "0"), 65536)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def _arena(self, method: str) -> bool:
            """Arena pages and score reports; True if the request was an arena request."""
            path = self.path.split("?", 1)[0]
            if method == "GET" and path.startswith("/arena/"):
                game = GAMES.get(path[len("/arena/") :])
                if game is None or game.native:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, ARENA_HTML.encode(), "text/html; charset=utf-8")
                return True
            if method == "POST" and path == "/arena/result":
                data = self._body()
                ok = server.arena.record(str(data.get("s", "")), data)
                self._json(200 if ok else 404, {"ok": ok})
                return True
            return False

        def _guard(self) -> Optional[str]:
            if not self._host_ok():
                return None
            if not self.path.startswith(base + "/") and self.path != base:
                self._send(404, b"not found", "text/plain")
                return None
            return self.path[len(base) :] or "/"

        def do_GET(self) -> None:
            if self.path.startswith("/arena/"):
                if self._host_ok():
                    self._arena("GET")
                return
            route = self._guard()
            if route is None:
                return
            route = route.split("?", 1)[0]
            if route == "/":
                html = CONTROL_HTML.replace("__BASE__", base)
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif route == "/api/status":
                self._json(200, server.state.snapshot())
            elif route == "/api/info":
                self._json(200, server.info())
            elif route == "/api/runs":
                self._json(200, recent_runs(state_dir() / "runs", limit=20))
            elif route.startswith("/api/runs/"):
                details = control_api.run_details(route[len("/api/runs/") :])
                self._json(200 if details else 404, details or {"error": "no such run"})
            elif route == "/api/config":
                self._json(200, control_api.config_view())
            elif route == "/api/doctor":
                self._json(200, control_api.doctor_view())
            elif route == "/api/screen":
                try:
                    jpeg = control_api.screen_jpeg()
                except Exception as exc:
                    self._json(503, {"error": str(exc)})
                    return
                if jpeg is None:
                    self._json(404, {"error": "screen preview needs the overlay"})
                else:
                    self._send(200, jpeg, "image/jpeg")
            elif route == "/api/bench":
                self._json(200, server.bench_status())
            elif route.startswith("/api/bench/"):
                report = server.bench_report(route[len("/api/bench/") :])
                self._json(200 if report else 404, report or {"error": "no such report"})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path.startswith("/arena/"):
                if self._host_ok():
                    self._arena("POST")
                return
            route = self._guard()
            if route is None:
                return
            body = self._body()
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
            elif route == "/api/config":
                try:
                    self._json(200, control_api.apply_config(body))
                except (ConfigError, ValueError, TypeError) as exc:
                    self._json(400, {"error": str(exc)})
            elif route == "/api/test-provider":
                self._json(200, control_api.test_provider())
            elif route == "/api/runs/clear":
                if server.state.running or server.bench.get("running"):
                    self._json(409, {"error": "a run is in progress"})
                else:
                    self._json(200, {"removed": control_api.clear_history()})
            elif route == "/api/bench":
                try:
                    seed = int(body.get("seed") or 1)
                    started = server.start_bench(str(body.get("suite", "quick")), seed)
                except (ConfigError, ValueError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                if started:
                    self._json(200, {"ok": True, "id": started})
                else:
                    self._json(409, {"error": "a run is already in progress"})
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
