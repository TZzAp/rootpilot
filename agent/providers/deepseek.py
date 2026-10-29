"""DeepSeek API (OpenAI-compatible Chat Completions) over raw HTTPS.

From DeepSeek's docs (checked 2026-10-29): base URL ``https://api.deepseek.com``, models
``deepseek-flash`` and ``deepseek-v4-pro``, thinking on by default. With tools in the request,
each assistant message's ``reasoning_content`` must be passed back in every later request or
the API returns a 400, so it is replayed from ``Message.raw``. No image input is documented, so
the model works from the UI tree text only.
"""

from __future__ import annotations

from typing import Any

from agent.providers.openai import OpenAIProvider
from agent.redact import Redactor
from agent.types import Message, ToolSpec

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"
    vision = False
    max_tokens_field = "max_tokens"
    replay_fields = ("reasoning_content",)
    key_hint = "DEEPSEEK_API_KEY or deepseek_api_key"
    default_model = DEFAULT_MODEL
    api_url = API_URL

    def __init__(
        self, api_key: str, model: str = "", *, effort: str = "", redactor: Redactor, **kw: Any
    ) -> None:
        super().__init__(api_key, model, redactor=redactor, **kw)
        self.effort = effort

    def build_request(
        self, system: str, messages: list[Message], tools: list[ToolSpec], max_output_tokens: int
    ) -> dict[str, Any]:
        body = super().build_request(system, messages, tools, max_output_tokens)
        # Thinking is on by default (effort "high"). "off" turns it off: much faster per step.
        if self.effort == "off":
            body["thinking"] = {"type": "disabled"}
        elif self.effort:
            body["reasoning_effort"] = {"medium": "high"}.get(self.effort, self.effort)
        return body
