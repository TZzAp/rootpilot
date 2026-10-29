"""Model providers."""

from __future__ import annotations

from agent.config import Config
from agent.errors import ConfigError
from agent.providers.base import Provider
from agent.redact import Redactor

PROVIDERS = ("anthropic", "deepseek", "openai", "compat", "chatgpt")


def make_provider(cfg: Config, redactor: Redactor) -> Provider:
    if cfg.provider == "anthropic":
        from agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(cfg.anthropic_api_key, cfg.model, redactor=redactor)
    if cfg.provider == "deepseek":
        from agent.providers.deepseek import DeepSeekProvider

        return DeepSeekProvider(cfg.deepseek_api_key, cfg.model, redactor=redactor)
    if cfg.provider == "openai":
        from agent.providers.openai import OpenAIProvider

        return OpenAIProvider(
            cfg.openai_api_key, cfg.model, redactor=redactor, keep_screenshots=cfg.keep_screenshots
        )
    if cfg.provider == "compat":
        from agent.providers.compat import CompatProvider

        return CompatProvider(
            cfg.compat_base_url,
            cfg.model,
            cfg.compat_api_key,
            redactor=redactor,
            keep_screenshots=cfg.keep_screenshots,
            vision=cfg.compat_vision,
        )
    if cfg.provider == "chatgpt":
        from agent.providers.chatgpt import ChatGPTProvider

        return ChatGPTProvider()
    raise ConfigError(f"unknown provider {cfg.provider!r}; choose one of {', '.join(PROVIDERS)}")
