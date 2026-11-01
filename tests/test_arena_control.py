from __future__ import annotations

import json
import re
import stat
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from agent import control_api
from agent.arena import (
    GAMES,
    SUITES,
    ArenaSessions,
    calculator_operands,
    score_game,
    task_for,
    totals,
)
from agent.backends.mock import DEMO_SCREENS, MockBackend
from agent.config import Config
from agent.errors import ConfigError
from agent.loop import Agent, RunResult
from agent.permissions import IRREVERSIBLE_WORDS
from agent.serve import WEBUI, ControlServer, ControlState
from agent.types import Usage
from tests.test_loop import _call, make_agent
from tests.test_serve import TOKEN, Live

# ---- arena ---------------------------------------------------------------------------


def test_arena_labels_never_trigger_confirmations() -> None:
    page = (WEBUI / "arena.html").read_text()
    words = json.loads(re.search(r"const WORDS = (\[.*?\]);", page, re.S).group(1))  # type: ignore[union-attr]
    labels = [*words, "OK", "Back", "DONE", "Tap", "Next", "type here", "#1", "Paste"]
    pattern = re.compile(
        r"(?i)(?<![\w-])(" + "|".join(re.escape(w) for w in IRREVERSIBLE_WORDS) + r")(?![\w-])"
    )
    assert len(words) == len(set(words)) >= 40
    assert [w for w in labels if pattern.search(w)] == []


def test_suites_and_tasks() -> None:
    assert set(SUITES["quick"]) <= set(SUITES["full"]) <= set(GAMES)
    url = "http://127.0.0.1:8366/arena/math?seed=7&s=abc&r=2"
    assert url in task_for(GAMES["math"], url, 7)
    a, b = calculator_operands(7)
    assert f"{a} × {b}" in task_for(GAMES["calculator"], "", 7)
    assert calculator_operands(7) == calculator_operands(7)


def test_sessions_only_accept_known_games() -> None:
    s = ArenaSessions()
    sid = s.create("math")
    assert not s.record("nope", {"game": "math", "correct": 2})
    assert not s.record(sid, {"game": "order", "correct": 2})  # wrong game for this session
    assert s.record(
        sid, {"game": "math", "correct": 1, "total": 2, "mistakes": 1, "finished": True}
    )
    assert s.result(sid) == {
        "game": "math",
        "correct": 1,
        "total": 2,
        "mistakes": 1,
        "ms": None,
        "finished": True,
    }


def test_scoring() -> None:
    run = RunResult("r1", "done: 896", True, "done", 6, Usage(1000, 200))
    page = {"correct": 3, "total": 4, "mistakes": 2, "finished": True}
    row = score_game(GAMES["word-hunt"], 7, page, run, 42.0)
    assert (row["correct"], row["total"], row["mistakes"], row["finished"]) == (3, 4, 2, True)
    assert row["steps"] == 6 and row["seconds"] == 42.0
    a, b = calculator_operands(7)
    good = RunResult("r2", f"The result is {a * b:,}.", True, "done", 5, Usage())
    assert score_game(GAMES["calculator"], 7, None, good, 10)["correct"] == 1
    bad = RunResult("r3", "I got 12", True, "done", 5, Usage())
    assert score_game(GAMES["calculator"], 7, None, bad, 10)["correct"] == 0
    # no page report at all (the agent never got there) scores zero, not an error
    assert score_game(GAMES["math"], 7, None, run, 5)["correct"] == 0
    t = totals([row, score_game(GAMES["calculator"], 7, None, good, 10)])
    assert t["score"] == 80.0 and t["correct"] == 4 and t["total"] == 5


# ---- control api ---------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ROOTPILOT_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_settings_round_trip_never_returns_keys(home: Path) -> None:
    key = "sk-" + "k" * 32
    view = control_api.apply_config(
        {
            "provider": "deepseek",
            "api_key": key,
            "effort": "off",
            "max_steps": 60,
            "max_minutes": 12,
            "max_tokens_k": 2000,
        }
    )
    assert view["provider"] == "deepseek" and view["keys"]["deepseek"] is True
    assert view["effort"] == "off" and view["max_steps"] == 60 and view["max_minutes"] == 12
    assert view["max_tokens_k"] == 2000
    assert key not in json.dumps(view)
    path = home / "config" / "rootpilot" / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["deepseek_api_key"] == key
    # switching provider drops the old provider's model name but keeps its key
    control_api.apply_config({"provider": "deepseek", "model": "deepseek-v4-pro"})
    view = control_api.apply_config({"provider": "anthropic"})
    assert view["model"] == "" and view["keys"] == {
        "anthropic": False,
        "deepseek": True,
        "openai": False,
        "compat": False,
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"provider": "chatgpt"},
        {"provider": "deepseek", "effort": "turbo"},
        {"provider": "deepseek", "model": "x; rm -rf /"},
        {"provider": "compat", "compat_base_url": "file:///etc/passwd"},
        {"provider": "deepseek", "api_key": "two words"},
    ],
)
def test_settings_validation(home: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        control_api.apply_config(bad)


def test_run_details_and_clear_history(home: Path) -> None:
    device = MockBackend.from_dir(DEMO_SCREENS)
    agent, _, _ = make_agent(device, [_call("done", {"answer": "ok", "success": True})], home)
    from agent.audit import AuditLog
    from agent.paths import state_dir
    from agent.redact import Redactor

    agent.audit = AuditLog.for_run(state_dir(), "20261029-000000-abcdef", Redactor())
    agent.run("say ok")
    agent.audit.close()
    d = control_api.run_details("20261029-000000-abcdef")
    assert d is not None and d["task"] == "say ok" and d["reason"] == "done"
    assert d["steps"][0]["kind"] == "model" and "done" in d["steps"][0]["calls"]
    assert control_api.run_details("../../etc/passwd") is None
    assert control_api.clear_history() == 1
    assert control_api.run_details("20261029-000000-abcdef") is None


# ---- server: arena pages and a whole benchmark ---------------------------------------


def bench_server(home: Path, monkeypatch: pytest.MonkeyPatch) -> ControlServer:
    def build(state: ControlState, task: str, overrides: Any = None, bench: bool = False) -> Agent:
        url = re.search(r"http://\S+", task)
        if url:  # stand in for Safari: the page reports a perfect round
            q = parse_qs(urlparse(url.group(0)).query)
            game = urlparse(url.group(0)).path.rsplit("/", 1)[-1]
            server.arena.record(
                q["s"][0], {"game": game, "correct": 1, "total": 1, "mistakes": 0, "finished": True}
            )
            answer = "score 1/1"
        else:
            a, b = calculator_operands(3)
            answer = str(a * b)
        agent, _, _ = make_agent(
            MockBackend.from_dir(DEMO_SCREENS),
            [_call("done", {"answer": answer, "success": True})],
            home,
        )
        agent.report = state.report
        assert overrides and overrides["max_steps"] > 0
        assert bench  # arena runs never wait for a human
        return agent

    monkeypatch.setattr(time, "sleep", lambda s: None)
    server = ControlServer(Config(serve_port=0), build_agent=build, token=TOKEN)
    return server


def test_arena_pages_and_results_endpoint(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = bench_server(home, monkeypatch)
    httpd = server.serve()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    live = Live(server, httpd.server_address[1])
    try:
        code, page = live.req("GET", "/arena/word-hunt?seed=7&s=x&r=3")
        assert code == 200 and "RootPilot Arena" in page
        assert live.req("GET", "/arena/calculator")[0] == 404  # native games have no page
        assert live.req("GET", "/arena/nope")[0] == 404
        assert live.req("POST", "/arena/result", {"s": "forged", "game": "math"})[0] == 404
        assert live.req("GET", "/arena/math", host="evil.example")[0] == 421
        sid = server.arena.create("math")
        assert live.req("POST", "/arena/result", {"s": sid, "game": "math", "correct": 2})[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_full_benchmark_run(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = bench_server(home, monkeypatch)
    assert server.start_bench("full", 3)
    assert not server.start_run("something else")  # busy while benchmarking
    # time.sleep is stubbed out for the bench thread; wait without it
    deadline = time.monotonic() + 10
    while server.bench.get("running") and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    report = server.bench["last"]
    assert [g["game"] for g in report["games"]] == list(SUITES["full"])
    assert report["totals"]["score"] == 100.0 and not report["aborted"]
    status = server.bench_status()
    assert status["reports"][0]["id"] == report["id"]
    saved = server.bench_report(report["id"])
    assert saved is not None and saved["seed"] == 3
    with pytest.raises(ConfigError):
        server.start_bench("nope", 1)
