from __future__ import annotations

import argparse
import io
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from agent.backends.mock import MockBackend
from agent.config import load_config
from agent.providers import make_provider
from agent.providers.deepseek import DeepSeekProvider
from agent.redact import MASK, Redactor
from agent.setup_wizard import setup
from agent.types import ImagePart, Message, TextPart, ToolResult
from tests.test_loop import ScriptedProvider, _call, make_agent
from tests.test_providers import TOOLS, FakeOpener, http_error

DS_KEY = "sk-" + "d" * 32

DS_REPLY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "The user wants Settings; launch it.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "launch_app",
                            "arguments": '{"bundle_id": "com.apple.Preferences"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
}


def _conversation(provider: DeepSeekProvider, opener: FakeOpener) -> list[Message]:
    msgs = [Message("user", [TextPart("Task: open Settings"), ImagePart(b"png")])]
    first = provider.complete("sys", msgs, TOOLS, 1000)
    msgs.append(
        Message("assistant", list(first.tool_calls), raw=first.raw, raw_provider="deepseek")
    )
    msgs.append(Message("user", [ToolResult("call_1", [TextPart("launched"), ImagePart(b"png")])]))
    return msgs


def test_deepseek_request_shape_and_reasoning_replay() -> None:
    opener = FakeOpener(DS_REPLY, DS_REPLY)
    p = DeepSeekProvider(DS_KEY, redactor=Redactor([DS_KEY]), opener=opener)
    assert p.model == "deepseek-flash" and not p.vision
    msgs = _conversation(p, opener)
    p.complete("sys", msgs, TOOLS, 1000)

    req = opener.requests[1]
    assert req.full_url == "https://api.deepseek.com/chat/completions"
    assert dict(req.header_items())["Authorization"] == f"Bearer {DS_KEY}"
    body = opener.body(1)
    assert body["model"] == "deepseek-flash"
    assert body["max_tokens"] == 1000 and "max_completion_tokens" not in body
    # reasoning_content from the tool-call turn is sent back (DeepSeek 400s otherwise)
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "The user wants Settings; launch it."
    assert assistant["tool_calls"][0]["function"]["name"] == "launch_app"
    # no image ever reaches a text-only model
    assert "image_url" not in json.dumps(body)
    # history is append-only across requests
    first, second = opener.body(0), opener.body(1)
    assert second["messages"][: len(first["messages"])] == first["messages"]


def test_deepseek_errors_are_redacted() -> None:
    opener = FakeOpener(http_error(401, f"Authentication Fails, key {DS_KEY} invalid"))
    p = DeepSeekProvider(DS_KEY, redactor=Redactor([DS_KEY]), opener=opener)
    with pytest.raises(Exception) as info:
        p.complete("sys", [Message("user", [TextPart("hi")])], TOOLS, 100)
    assert DS_KEY not in str(info.value) and MASK in str(info.value)


def test_factory_and_env_key(tmp_path: Path) -> None:
    cfg = load_config(
        tmp_path / "none.json", env={"DEEPSEEK_API_KEY": DS_KEY, "ROOTPILOT_PROVIDER": "deepseek"}
    )
    provider = make_provider(cfg, Redactor(cfg.secrets()))
    assert isinstance(provider, DeepSeekProvider) and DS_KEY in cfg.secrets()
    with pytest.raises(Exception, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider("", redactor=Redactor())


def test_loop_skips_screenshots_for_text_only_models(
    mock_device: MockBackend, tmp_path: Path
) -> None:
    agent, provider, _ = make_agent(
        mock_device, [_call("done", {"answer": "x", "success": True})], tmp_path
    )
    provider.vision = False
    agent.run("x")
    assert not any(op == "screenshot" for op, _ in mock_device.calls)
    assert "ImagePart" not in "".join(provider.requests[0][2])


# ---- rootpilot setup -------------------------------------------------------------------


def _args(tmp_path: Path, **kw: Any) -> argparse.Namespace:
    base = {
        "config": str(tmp_path / "rp" / "config.json"),
        "provider": None,
        "model": None,
        "backend": None,
        "key_stdin": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_setup_menu_choose_deepseek(tmp_path: Path) -> None:
    answers = iter(["2", ""])  # menu: DeepSeek; model: accept default
    out = io.StringIO()
    code = setup(_args(tmp_path), out, read=lambda p: next(answers), read_secret=lambda p: DS_KEY)
    assert code == 0, out.getvalue()
    path = tmp_path / "rp" / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data == {
        "deepseek_api_key": DS_KEY,
        "provider": "deepseek",
        "model": "deepseek-flash",
        "backend": "zxtouch",
    }
    assert DS_KEY not in out.getvalue()
    # and the result loads (the 0600 check passes)
    assert load_config(path, env={}).deepseek_api_key == DS_KEY


def test_setup_key_stdin_keeps_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "rp" / "config.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "provider": "anthropic",
                "anthropic_api_key": "sk-ant-x" + "y" * 20,
                "backend": "mock",
                "max_steps": 12,
            }
        )
    )
    path.chmod(0o600)
    out = io.StringIO()
    code = setup(
        _args(tmp_path, provider="deepseek", key_stdin=True), out, stdin=io.StringIO(DS_KEY + "\n")
    )
    assert code == 0, out.getvalue()
    data = json.loads(path.read_text())
    assert data["provider"] == "deepseek" and data["deepseek_api_key"] == DS_KEY
    assert data["model"] == "deepseek-flash"  # not the old Claude model
    assert (
        data["anthropic_api_key"].startswith("sk-ant-")
        and data["max_steps"] == 12
        and data["backend"] == "mock"
    )


def test_setup_requires_a_key(tmp_path: Path) -> None:
    out = io.StringIO()
    code = setup(
        _args(tmp_path, provider="deepseek"), out, read=lambda p: "", read_secret=lambda p: ""
    )
    assert code == 1 and "no API key" in out.getvalue()
    assert not (tmp_path / "rp" / "config.json").exists()


def test_scripted_provider_is_a_provider() -> None:
    # keeps the test double honest about the Provider protocol's vision flag
    assert ScriptedProvider([]).vision is True
