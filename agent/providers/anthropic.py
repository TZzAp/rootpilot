"""Anthropic Messages API over raw HTTPS (SPEC §3.6).

Raw HTTP rather than the official SDK because the SDK needs Python >= 3.10 and compiled wheels,
neither of which the device has. Requests follow the current API:

- ``anthropic-version: 2026-10-21``; no sampling parameters (they 400 on that version).
- Thinking is always on for ``claude-opus-5-5``; depth is set with ``output_config.effort``.
- Assistant turns are replayed verbatim from ``Message.raw`` (thinking blocks keep their
  signatures) and the history is append-only. Old screenshots are cleared server-side with
  context editing, which doesn't count as a history edit.
- Server-side refusal fallbacks are on (``fallbacks: "default"``).
"""

from __future__ import annotations

from typing import Any

from agent.errors import ProviderError
from agent.providers.base import b64
from agent.providers.http import Opener, Sleep, post_json
from agent.redact import Redactor
from agent.types import (
    Completion,
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
BETAS = (
    "server-side-fallback-2026-07-01",  # fallbacks: "default"
    "context-management-2025-06-27",  # clear_tool_uses_20250919
)


def _content(parts: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            out.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": b64(part.data),
                    },
                }
            )
        elif isinstance(part, ToolCall):
            out.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
        elif isinstance(part, ToolResult):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": part.call_id,
                    "content": _content(list(part.content)),
                    "is_error": part.is_error,
                }
            )
    return out


class AnthropicProvider:
    name = "anthropic"
    vision = True

    def __init__(
        self,
        api_key: str,
        model: str = "",
        effort: str = DEFAULT_EFFORT,
        *,
        redactor: Redactor,
        url: str = API_URL,
        opener: Opener | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "no Anthropic API key: set ANTHROPIC_API_KEY or anthropic_api_key in the config"
            )
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self._key = api_key
        self._url = url
        self._redactor = redactor
        self._http: dict[str, Any] = {}
        if opener is not None:
            self._http["opener"] = opener
        if sleep is not None:
            self._http["sleep"] = sleep

    def build_request(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_output_tokens: int
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "assistant" and msg.raw_provider == self.name and msg.raw is not None:
                content = msg.raw
            else:
                content = _content(msg.parts)
            wire.append({"role": msg.role, "content": content})
        return {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": wire,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            "tool_choice": {"type": "auto"},
            "output_config": {"effort": self.effort},
            "fallbacks": "default",
            # Each step resends the whole run; caching the prefix makes that cheap.
            "cache_control": {"type": "ephemeral"},
            "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        }

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_output_tokens: int
    ) -> Completion:
        body = self.build_request(system, messages, tools, max_output_tokens)
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "anthropic-beta": ",".join(BETAS),
        }
        data = post_json(self._url, body, headers, redactor=self._redactor, **self._http)
        return self.parse_response(data)

    @staticmethod
    def parse_response(data: dict[str, Any]) -> Completion:
        if data.get("type") == "error":
            raise ProviderError(str(data.get("error")))
        blocks = data.get("content") or []
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        calls = [
            ToolCall(str(b["id"]), str(b["name"]), dict(b.get("input") or {}))
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}
        stop = str(data.get("stop_reason") or "")
        refusal = ""
        if stop == "refusal":
            details = data.get("stop_details") or {}
            refusal = str(details.get("explanation") or details.get("category") or "declined")
        return Completion(
            text="\n".join(t for t in texts if t),
            tool_calls=calls,
            usage=Usage(
                sum(
                    int(usage.get(k) or 0)
                    for k in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                ),
                int(usage.get("output_tokens") or 0),
            ),
            stop_reason=stop,
            raw=blocks,
            refusal=refusal,
        )
