"""Tool implementations. Permission checks happen before these run (in the loop / MCP server)."""

from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from agent.backends.base import Backend
from agent.errors import ToolInputError
from agent.paths import jbroot
from agent.perception.axtree import RenderedTree
from agent.redact import Redactor
from agent.tools import targets
from agent.tools.registry import ToolOutcome
from agent.types import Button, ImagePart, TextPart, UINode

_SHELL_OUTPUT_LIMIT = 8000


@dataclass
class ToolContext:
    backend: Backend
    # Returns observation parts; the loop owns perception state (refs, screenshot history).
    observe: Callable[[bool, bool], list[TextPart | ImagePart]]
    ask_user: Callable[[str], str]
    redactor: Redactor
    rendered: Optional[RenderedTree] = None
    root: Optional[UINode] = None


def _str_arg(args: dict[str, Any], name: str, max_len: int = 4096) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise ToolInputError(f"{name} must be a non-empty string")
    if len(value) > max_len:
        raise ToolInputError(f"{name} is longer than {max_len} characters")
    return value


def _int_arg(args: dict[str, Any], name: str, default: int, lo: int, hi: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"{name} must be a number")
    return max(lo, min(hi, int(value)))


def _num(args: dict[str, Any], name: str) -> float:
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"{name} must be a number")
    return float(value)


def observe(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    parts = ctx.observe(
        bool(args.get("include_screenshot", True)), bool(args.get("include_tree", True))
    )
    return ToolOutcome(parts)


def tap(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    t = targets.resolve(args, ctx.rendered, ctx.root)
    ctx.backend.tap(t.x, t.y)
    return ToolOutcome.text(f"tapped ({round(t.x)},{round(t.y)})")


def long_press(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    t = targets.resolve(args, ctx.rendered, ctx.root)
    duration = _int_arg(args, "duration_ms", 800, 100, 5000)
    ctx.backend.long_press(t.x, t.y, duration)
    return ToolOutcome.text(f"long-pressed ({round(t.x)},{round(t.y)}) for {duration} ms")


def swipe(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    x1, y1, x2, y2 = (_num(args, k) for k in ("x1", "y1", "x2", "y2"))
    duration = _int_arg(args, "duration_ms", 300, 50, 3000)
    ctx.backend.swipe(x1, y1, x2, y2, duration)
    return ToolOutcome.text(f"swiped ({round(x1)},{round(y1)}) → ({round(x2)},{round(y2)})")


def type_text(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    text = _str_arg(args, "text", 2000)
    if args.get("ref") is not None:
        t = targets.resolve(args, ctx.rendered, ctx.root)
        ctx.backend.tap(t.x, t.y)
        time.sleep(0.3)  # let the keyboard come up
    ctx.backend.type_text(text)
    return ToolOutcome.text(f"typed {len(text)} characters")


def press_button(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    try:
        button = Button(args.get("button"))
    except ValueError as exc:
        raise ToolInputError("button must be one of: home, lock, volume_up, volume_down") from exc
    ctx.backend.press_button(button)
    return ToolOutcome.text(f"pressed {button.value}")


def launch_app(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    bundle_id = _str_arg(args, "bundle_id", 255)
    ctx.backend.launch_app(bundle_id)
    return ToolOutcome.text(f"launched {bundle_id}")


def open_url(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    url = _str_arg(args, "url", 2048)
    ctx.backend.open_url(url)
    return ToolOutcome.text(f"opened {url}")


def clipboard_get(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    return ToolOutcome([TextPart(ctx.backend.clipboard_get())])


def clipboard_set(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    text = args.get("text")
    if not isinstance(text, str):
        raise ToolInputError("text must be a string")
    ctx.backend.clipboard_set(text)
    return ToolOutcome.text(f"clipboard set ({len(text)} characters)")


def wait(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    seconds = min(10.0, max(0.1, _num(args, "seconds")))
    time.sleep(seconds)
    return ToolOutcome(ctx.observe(True, True))


def shell(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    command = _str_arg(args, "command", 4000)
    timeout = _int_arg(args, "timeout_s", 20, 1, 120)
    sh = jbroot() / "bin" / "sh"
    try:
        result = subprocess.run(
            [str(sh) if sh.exists() else "/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolOutcome.text(f"command timed out after {timeout} s", is_error=True)
    out = (result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else ""))[
        :_SHELL_OUTPUT_LIMIT
    ]
    return ToolOutcome(
        [TextPart(f"exit {result.returncode}\n{ctx.redactor.text(out)}")],
        is_error=result.returncode != 0,
    )


def ask_user(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    return ToolOutcome.text("user answered: " + ctx.ask_user(_str_arg(args, "question", 1000)))


def done(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    answer = args.get("answer", "")
    success = bool(args.get("success", True))
    out = ToolOutcome.text("run finished")
    out.finished = (str(answer), success)
    return out


def clipboard_fingerprint(text: str) -> str:
    """What the audit log records instead of clipboard contents."""
    return f"{len(text)} chars, sha256 {hashlib.sha256(text.encode()).hexdigest()[:12]}"
