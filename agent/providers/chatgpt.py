"""Sign in with ChatGPT — planned for M4, not implemented.

M4 will follow OpenAI's official "Sign in with ChatGPT" integration guide (device-code login)
and nothing else: no reuse of tokens from other apps and no imitation of first-party clients.
"""

from __future__ import annotations

from typing import Any, NoReturn


class ChatGPTProvider:
    name = "chatgpt"
    model = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(
            "the chatgpt provider arrives in M4 (Sign in with ChatGPT); use --provider openai "
            "with an API key for now"
        )

    def complete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise NotImplementedError
