from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from agent.errors import ProviderError
from agent.providers.anthropic import API_VERSION, AnthropicProvider
from agent.providers.compat import CompatProvider
from agent.providers.http import USER_AGENT
from agent.providers.openai import OpenAIProvider
from agent.redact import MASK, Redactor
from agent.types import ImagePart, Message, TextPart, ToolCall, ToolResult, ToolSpec

KEY = "sk-ant-api03-" + "Z" * 40
TOOLS = [ToolSpec("tap", "Tap", {"type": "object", "properties": {}})]


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class FakeOpener:
    """Records requests and returns queued responses (dicts) or raises queued HTTP errors."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, urllib.error.HTTPError):
            raise item
        return FakeResponse(json.dumps(item).encode())

    def body(self, i: int = -1) -> dict[str, Any]:
        data = self.requests[i].data
        assert isinstance(data, bytes)
        result: dict[str, Any] = json.loads(data)
        return result


def http_error(
    code: int, body: str, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    msg = urllib.request.Request("https://x").get_full_url()
    import email.message

    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError(msg, code, "err", hdrs, io.BytesIO(body.encode()))


ANTHROPIC_REPLY = {
    "type": "message",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
        {"type": "text", "text": "Opening Settings."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "launch_app",
            "input": {"bundle_id": "com.apple.Preferences"},
        },
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 50},
}


def history() -> list[Message]:
    first = AnthropicProvider.parse_response(ANTHROPIC_REPLY)
    return [
        Message("user", [TextPart("Task: open Settings")]),
        Message(
            "assistant",
            [TextPart(first.text), *first.tool_calls],
            raw=first.raw,
            raw_provider="anthropic",
        ),
        Message(
            "user",
            [ToolResult("toolu_1", [TextPart("launched"), ImagePart(b"\x89PNGfake")])],
        ),
    ]


def test_anthropic_request_shape_and_headers() -> None:
    opener = FakeOpener(ANTHROPIC_REPLY)
    p = AnthropicProvider(KEY, redactor=Redactor([KEY]), opener=opener)
    completion = p.complete("sys", history(), TOOLS, 16000)
    req = opener.requests[0]
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers["x-api-key"] == KEY
    assert headers["anthropic-version"] == API_VERSION == "2026-10-21"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert headers["user-agent"] == USER_AGENT and "claude" not in USER_AGENT.lower()
    body = opener.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert body["tool_choice"] == {"type": "auto"}
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in body
    # the assistant turn is replayed verbatim, thinking signature included
    assert body["messages"][1]["content"] == ANTHROPIC_REPLY["content"]
    result = body["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert result["content"][1]["type"] == "image"
    assert completion.usage.input_tokens == 1000
    assert completion.tool_calls[0].arguments == {"bundle_id": "com.apple.Preferences"}


def test_anthropic_history_is_append_only() -> None:
    """Consecutive requests must share a byte-identical prefix (preserved thinking)."""
    opener = FakeOpener(ANTHROPIC_REPLY, ANTHROPIC_REPLY)
    p = AnthropicProvider(KEY, redactor=Redactor(), opener=opener)
    msgs = history()
    p.complete("sys", msgs, TOOLS, 16000)
    msgs.append(
        Message(
            "assistant",
            [TextPart("more")],
            raw=[{"type": "text", "text": "more"}],
            raw_provider="anthropic",
        )
    )
    msgs.append(Message("user", [TextPart("next")]))
    p.complete("sys", msgs, TOOLS, 16000)
    first, second = opener.body(0), opener.body(1)
    assert second["system"] == first["system"] and second["tools"] == first["tools"]
    assert second["messages"][: len(first["messages"])] == first["messages"]


def test_anthropic_refusal() -> None:
    reply = {
        "content": [],
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
        "usage": {},
    }
    completion = AnthropicProvider.parse_response(reply)
    assert completion.refusal == "cyber"


def test_retry_after_then_success_and_errors_are_redacted() -> None:
    sleeps: list[float] = []
    opener = FakeOpener(http_error(529, "overloaded", {"retry-after": "3"}), ANTHROPIC_REPLY)
    p = AnthropicProvider(KEY, redactor=Redactor([KEY]), opener=opener, sleep=sleeps.append)
    p.complete("sys", history(), TOOLS, 100)
    assert sleeps == [3.0]
    bad = FakeOpener(http_error(401, f"invalid x-api-key {KEY}"))
    p = AnthropicProvider(KEY, redactor=Redactor([KEY]), opener=bad)
    with pytest.raises(ProviderError) as info:
        p.complete("sys", history(), TOOLS, 100)
    assert KEY not in str(info.value) and MASK in str(info.value)


def test_missing_keys_and_models() -> None:
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider("", redactor=Redactor())
    with pytest.raises(ProviderError, match="model"):
        OpenAIProvider("sk-x", "", redactor=Redactor())
    with pytest.raises(ProviderError, match="compat_base_url"):
        CompatProvider("", "llama", redactor=Redactor())


OPENAI_REPLY = {
    "choices": [
        {
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "tap", "arguments": '{"ref": "e2"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}


def test_openai_moves_images_out_of_tool_messages_and_trims_old_ones() -> None:
    opener = FakeOpener(OPENAI_REPLY)
    p = OpenAIProvider(
        "sk-test", "gpt-test", redactor=Redactor(), keep_screenshots=1, opener=opener
    )
    msgs = [
        Message("user", [TextPart("task"), ImagePart(b"old")]),
        Message("assistant", [ToolCall("call_0", "observe", {})]),
        Message("user", [ToolResult("call_0", [TextPart("tree"), ImagePart(b"new")])]),
    ]
    completion = p.complete("sys", msgs, TOOLS, 500)
    body = opener.body()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert body["messages"][3]["content"] == "tree"
    first_user = json.dumps(body["messages"][1])
    assert "older screenshot omitted" in first_user
    assert "image_url" in json.dumps(body["messages"][4])
    assert completion.tool_calls[0].arguments == {"ref": "e2"}


def test_compat_uses_base_url_without_key() -> None:
    opener = FakeOpener(OPENAI_REPLY)
    p = CompatProvider("http://127.0.0.1:8080/v1/", "llama", redactor=Redactor(), opener=opener)
    p.complete("sys", [Message("user", [TextPart("hi")])], TOOLS, 100)
    req = opener.requests[0]
    assert req.full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert "Authorization" not in dict(req.header_items())
