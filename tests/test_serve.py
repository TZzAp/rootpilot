from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent.audit import AuditLog
from agent.backends.mock import DEMO_SCREENS, MockBackend
from agent.config import Config
from agent.errors import ConfigError
from agent.loop import Agent
from agent.paths import state_dir, stop_file
from agent.redact import Redactor
from agent.serve import ControlServer, ControlState, server_ask, server_confirm
from tests.test_loop import _call, make_agent, tap_label

TOKEN = "test-token-123"
SECRET_KEY = "sk-ant-api03-" + "S" * 40


class Live:
    def __init__(self, server: ControlServer, port: int) -> None:
        self.server, self.port = server, port

    def req(
        self, method: str, path: str, body: Any = None, host: str = "127.0.0.1"
    ) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Host": f"{host}:{self.port}", "Content-Type": "application/json"}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, raw.decode()

    def wait(self, pred: Any, timeout: float = 5.0) -> dict[str, Any]:
        end = time.time() + timeout
        while time.time() < end:
            _, s = self.req("GET", f"/{TOKEN}/api/status")
            if pred(s):
                return dict(s)
            time.sleep(0.02)
        raise AssertionError(f"timed out; last status: {s}")


def start(tmp_path: Path, steps: list[Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[Live]:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ROOTPILOT_BACKEND", "mock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET_KEY)

    def build(state: ControlState, task: str) -> Agent:
        agent, _, _ = make_agent(MockBackend.from_dir(DEMO_SCREENS), steps, tmp_path)
        agent.confirm = server_confirm(state)
        agent.ask_user = server_ask(state)
        agent.report = state.report
        agent.stop_file = stop_file()  # the file the server's Stop button writes
        agent.audit = AuditLog.for_run(
            state_dir(), agent.run_id, Redactor()
        )  # where /api/runs looks
        return agent

    server = ControlServer(Config(serve_port=0), build_agent=build, token=TOKEN)
    httpd = server.serve()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield Live(server, httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Live]:
    steps = [
        _call("launch_app", {"bundle_id": "com.apple.Preferences"}),
        tap_label("General"),
        tap_label("About"),
        _call("done", {"answer": "iOS 16.7.11", "success": True}),
    ]
    yield from start(tmp_path, steps, monkeypatch)


def test_wrong_token_and_bad_host_are_rejected(flow: Live) -> None:
    assert flow.req("GET", "/")[0] == 404
    assert flow.req("GET", "/wrong-token/api/status")[0] == 404
    assert flow.req("GET", f"/{TOKEN}/api/status", host="evil.example")[0] == 421
    code, page = flow.req("GET", f"/{TOKEN}/")
    assert code == 200 and "RootPilot" in page and "__BASE__" not in page
    conn = http.client.HTTPConnection("127.0.0.1", flow.port, timeout=5)
    conn.request("GET", f"/{TOKEN}/", headers={"Host": f"127.0.0.1:{flow.port}"})
    assert conn.getresponse().getheader("Cache-Control") == "no-store"
    conn.close()


def test_refuses_non_loopback_bind() -> None:
    with pytest.raises(ConfigError, match="loopback-only"):
        ControlServer(Config(serve_port=0), token=TOKEN).serve("0.0.0.0")


def test_run_from_the_page(flow: Live) -> None:
    assert flow.req("POST", f"/{TOKEN}/api/run", {"task": ""})[0] == 400
    assert flow.req("POST", f"/{TOKEN}/api/run", {"task": "iOS version?"}) == (200, {"ok": True})
    status = flow.wait(lambda s: s["result"] is not None)
    assert status["result"]["success"] and status["result"]["answer"] == "iOS 16.7.11"
    assert any("tap e" in line and "About" in line for line in status["lines"])
    assert not status["running"]


def test_confirm_prompt_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps = [
        _call("clipboard_get", {}),  # asks by default
        _call("done", {"answer": "read it", "success": True}),
    ]
    for live in start(tmp_path, steps, monkeypatch):
        live.req("POST", f"/{TOKEN}/api/run", {"task": "read my clipboard"})
        status = live.wait(lambda s: s["pending"] is not None)
        pending = status["pending"]
        assert pending["tool"] == "clipboard_get" and pending["allow_always"]
        assert live.req("POST", f"/{TOKEN}/api/confirm", {"id": "nope", "answer": "yes"})[0] == 409
        live.req("POST", f"/{TOKEN}/api/confirm", {"id": pending["id"], "answer": "yes"})
        status = live.wait(lambda s: s["result"] is not None)
        assert status["result"]["success"]
        assert not any(line.startswith("⛔") for line in status["lines"])


def test_stop_releases_a_waiting_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps = [_call("clipboard_get", {}), _call("done", {"answer": "x", "success": True})]
    for live in start(tmp_path, steps, monkeypatch):
        live.req("POST", f"/{TOKEN}/api/run", {"task": "x"})
        live.wait(lambda s: s["pending"] is not None)
        assert live.req("POST", f"/{TOKEN}/api/run", {"task": "y"})[0] == 409  # busy
        live.req("POST", f"/{TOKEN}/api/stop", {})
        status = live.wait(lambda s: s["result"] is not None)
        assert status["result"]["reason"] == "stopped"
        assert any(line.startswith("⛔") for line in status["lines"])  # prompt was declined


def test_info_and_history(flow: Live) -> None:
    code, info = flow.req("GET", f"/{TOKEN}/api/info")
    assert code == 200
    assert info["provider"] == "anthropic" and info["backend"] == "mock"
    assert info["model"] == "claude-opus-5-5" and info["overlay"]["installed"] is False
    assert info["started"] == flow.server.started
    assert SECRET_KEY not in json.dumps(info)  # the page never sees keys
    assert flow.req("GET", f"/{TOKEN}/api/runs") == (200, [])
    flow.req("POST", f"/{TOKEN}/api/run", {"task": "iOS version?"})
    flow.wait(lambda s: s["result"] is not None)
    code, runs = flow.req("GET", f"/{TOKEN}/api/runs")
    assert code == 200 and len(runs) == 1
    assert runs[0]["task"] == "iOS version?" and runs[0]["success"] is True
    assert runs[0]["answer"] == "iOS 16.7.11" and runs[0]["steps"] == 4
