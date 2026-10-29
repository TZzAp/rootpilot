"""Provider interface: one model API behind a provider-neutral message format (SPEC §3.6)."""

from __future__ import annotations

import base64
from typing import Protocol

from agent.types import Completion, Message, ToolSpec


class Provider(Protocol):
    name: str
    model: str
    vision: bool  # accepts image input; if not, the loop sends the UI tree only

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_output_tokens: int,
    ) -> Completion:
        """Send the whole conversation. ``messages`` is append-only across calls in a run."""
        ...


def b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")
