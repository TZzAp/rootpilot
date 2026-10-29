"""Any OpenAI-compatible Chat Completions server (llama.cpp, vLLM, Ollama, LM Studio, ...)."""

from __future__ import annotations

from typing import Any

from agent.errors import ProviderError
from agent.providers.openai import OpenAIProvider
from agent.redact import Redactor


class CompatProvider(OpenAIProvider):
    name = "compat"

    def __init__(
        self, base_url: str, model: str, api_key: str = "", *, redactor: Redactor, **kw: Any
    ) -> None:
        if not base_url:
            raise ProviderError(
                "set compat_base_url (e.g. http://127.0.0.1:8080/v1) for the compat provider"
            )
        super().__init__(
            api_key, model, redactor=redactor, base_url=base_url, require_key=False, **kw
        )
