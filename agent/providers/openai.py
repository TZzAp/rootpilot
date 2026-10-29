"""OpenAI Chat Completions API (tools + image input) over raw HTTPS (SPEC §3.6).

Chat Completions only accepts text in ``tool`` messages, so screenshots from a tool result are
sent in a follow-up ``user`` message. Unlike Anthropic, there is no thinking-block binding, so
older screenshots are replaced with a placeholder to keep requests small.

Subclasses (DeepSeek, OpenAI-compatible servers) adjust the class attributes below.
"""

from __future__ import annotations

import json
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

API_URL = "https://api.openai.com/v1/chat/completions"


def _image(part: ImagePart) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{part.media_type};base64,{b64(part.data)}"},
    }


class OpenAIProvider:
    name = "openai"
    vision = True  # accepts image input; the loop only takes screenshots for vision models
    max_tokens_field = "max_completion_tokens"
    # Fields of a returned assistant message that must be sent back verbatim on later requests.
    replay_fields: tuple[str, ...] = ()
    key_hint = "OPENAI_API_KEY or openai_api_key"
    default_model = ""
    api_url = API_URL

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        redactor: Redactor,
        base_url: str = "",
        keep_screenshots: int = 3,
        require_key: bool = True,
        vision: bool | None = None,
        opener: Opener | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        if require_key and not api_key:
            raise ProviderError(f"no {self.name} API key: set {self.key_hint} in the config")
        model = model or self.default_model
        if not model:
            raise ProviderError(
                f"set a model for the {self.name} provider (--model or 'model' in config)"
            )
        if vision is not None:
            self.vision = vision
        self.model = model
        self._key = api_key
        self._url = (base_url.rstrip("/") + "/chat/completions") if base_url else self.api_url
        self._keep = keep_screenshots
        self._redactor = redactor
        self._http: dict[str, Any] = {}
        if opener is not None:
            self._http["opener"] = opener
        if sleep is not None:
            self._http["sleep"] = sleep

    def build_request(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_output_tokens: int
    ) -> dict[str, Any]:
        # Count images newest-first so only the last N survive.
        image_budget = self._keep
        keep: set[int] = set()
        for msg in reversed(messages):
            for part in reversed(_flat_parts(msg)):
                if isinstance(part, ImagePart) and image_budget > 0:
                    keep.add(id(part))
                    image_budget -= 1

        def image_or_note(part: ImagePart) -> dict[str, Any]:
            if not self.vision:
                return {"type": "text", "text": "[screenshot omitted: model has no image input]"}
            if id(part) in keep:
                return _image(part)
            return {"type": "text", "text": "[older screenshot omitted]"}

        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for msg in messages:
            if msg.role == "assistant":
                text = "\n".join(p.text for p in msg.parts if isinstance(p, TextPart))
                calls = [p for p in msg.parts if isinstance(p, ToolCall)]
                entry: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in calls
                    ]
                if msg.raw_provider == self.name and isinstance(msg.raw, dict):
                    for name in self.replay_fields:
                        if name in msg.raw:
                            entry[name] = msg.raw[name]
                wire.append(entry)
                continue
            follow_up: list[dict[str, Any]] = []
            user_content: list[dict[str, Any]] = []
            for part in msg.parts:
                if isinstance(part, ToolResult):
                    texts = [p.text for p in part.content if isinstance(p, TextPart)]
                    prefix = "ERROR: " if part.is_error else ""
                    wire.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.call_id,
                            "content": prefix + "\n".join(texts),
                        }
                    )
                    follow_up += [
                        image_or_note(p) for p in part.content if isinstance(p, ImagePart)
                    ]
                elif isinstance(part, TextPart):
                    user_content.append({"type": "text", "text": part.text})
                elif isinstance(part, ImagePart):
                    user_content.append(image_or_note(part))
            if follow_up:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Screenshot from the last tool call:"},
                            *follow_up,
                        ],
                    }
                )
            if user_content:
                wire.append({"role": "user", "content": user_content})
        return {
            "model": self.model,
            "messages": wire,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ],
            self.max_tokens_field: max_output_tokens,
        }

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_output_tokens: int
    ) -> Completion:
        body = self.build_request(system, messages, tools, max_output_tokens)
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        data = post_json(self._url, body, headers, redactor=self._redactor, **self._http)
        return self.parse_response(data)

    @staticmethod
    def parse_response(data: dict[str, Any]) -> Completion:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"no choices in response: {str(data)[:300]}")
        message = choices[0].get("message") or {}
        calls: list[ToolCall] = []
        for c in message.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_invalid_json": fn.get("arguments")}
            calls.append(
                ToolCall(
                    str(c.get("id")), str(fn.get("name")), args if isinstance(args, dict) else {}
                )
            )
        usage = data.get("usage") or {}
        return Completion(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            usage=Usage(
                int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
            ),
            stop_reason=str(choices[0].get("finish_reason") or ""),
            raw=message,
            refusal=str(message.get("refusal") or ""),
        )


def _flat_parts(msg: Message) -> list[Any]:
    out: list[Any] = []
    for part in msg.parts:
        out.extend(part.content if isinstance(part, ToolResult) else [part])
    return out
