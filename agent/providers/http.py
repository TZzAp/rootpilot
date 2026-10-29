"""Small JSON-over-HTTPS client on urllib with retries (SDKs need Python >= 3.10 and wheels)."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from agent import __version__
from agent.errors import ProviderError
from agent.redact import Redactor

RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
# Honest client identification; RootPilot never presents itself as another client.
USER_AGENT = f"rootpilot/{__version__}"

Sleep = Callable[[float], None]
Opener = Callable[..., Any]


def _retry_after(headers: Any) -> float | None:
    value = headers.get("retry-after") if headers is not None else None
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def post_json(
    url: str,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    redactor: Redactor,
    timeout: float = 300.0,
    max_retries: int = 4,
    sleep: Sleep = time.sleep,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, Any]:
    if not url.startswith(("https://", "http://")):
        raise ProviderError(f"refusing non-HTTP provider URL {url!r}")
    data = json.dumps(body).encode("utf-8")
    all_headers = {"content-type": "application/json", "user-agent": USER_AGENT, **headers}
    attempt = 0
    while True:
        request = urllib.request.Request(  # noqa: S310 - scheme checked above
            url, data=data, headers=all_headers, method="POST"
        )
        delay: float | None = None
        try:
            with opener(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ProviderError("provider returned a non-object JSON response")
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            if exc.code not in RETRY_STATUS or attempt >= max_retries:
                raise ProviderError(
                    f"HTTP {exc.code} from {url}: {redactor.text(detail)}"
                ) from None
            delay = _retry_after(exc.headers)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt >= max_retries:
                raise ProviderError(f"cannot reach {url}: {redactor.text(str(exc))}") from None
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from {url}: {exc}") from None
        attempt += 1
        if delay is None:
            delay = min(60.0, 2.0**attempt) + random.uniform(0, 1)  # noqa: S311
        sleep(min(delay, 120.0))
