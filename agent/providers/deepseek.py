"""DeepSeek API (OpenAI-compatible Chat Completions) over raw HTTPS.

From DeepSeek's docs (checked 2026-10-29): base URL ``https://api.deepseek.com``, models
``deepseek-flash`` and ``deepseek-v4-pro``, thinking on by default. ``reasoning_content`` from
tool-call turns is replayed from ``Message.raw`` for the newest turns; older ones are replaced
by a placeholder. Replaying all of it made a 58-step game send 90k tokens per request by the end
(measured 2026-11-01); the API accepted placeholders and even omitted fields in tests that day.
Image input: ``deepseek-flash`` is V4.1 Flash, which reads images ("native multimodal visual
understanding", 2026-09-10; the models endpoint lists image input). Images go in OpenAI's
``image_url`` form; a 375x812 screenshot cost ~230 input tokens (tested 2026-11-01).
``deepseek-v4-pro`` lists text only: run it with ``screenshots: false``.
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
    vision = True  # V4.1 Flash (2026-09-10) reads images; checked with a phone screenshot
    max_tokens_field = "max_tokens"
    replay_fields = ("reasoning_content",)
    replay_keep = 4
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
