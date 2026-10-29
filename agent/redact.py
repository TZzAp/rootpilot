"""Mask secrets before anything is logged, printed or stored (SPEC §3.7)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

MASK = "***REDACTED***"

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"(?i)((?:authorization|x-api-key|api[-_]?key)\s*[:=]\s*)\S+"),
]

_SENSITIVE_KEY = re.compile(r"(?i)(key|token|secret|password|passwd|authorization)")


class Redactor:
    def __init__(self, literals: Iterable[str] = ()) -> None:
        # Short literals would mask ordinary words; only mask values that look like secrets.
        self._literals = sorted({s for s in literals if s and len(s) >= 6}, key=len, reverse=True)

    def add_literal(self, secret: str) -> None:
        if secret and len(secret) >= 6 and secret not in self._literals:
            self._literals.append(secret)
            self._literals.sort(key=len, reverse=True)

    def text(self, value: str) -> str:
        for literal in self._literals:
            value = value.replace(literal, MASK)
        for pattern in _PATTERNS:
            if pattern.groups:
                value = pattern.sub(lambda m: m.group(1) + MASK, value)
            else:
                value = pattern.sub(MASK, value)
        return value

    def obj(self, value: Any) -> Any:
        """Recursively redact a JSON-like structure. Values under secret-looking keys are masked."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                key = str(k)
                if _SENSITIVE_KEY.search(key) and isinstance(v, str) and v:
                    out[key] = MASK
                else:
                    out[key] = self.obj(v)
            return out
        if isinstance(value, (list, tuple)):
            return [self.obj(v) for v in value]
        return value
