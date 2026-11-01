"""Tool implementations. Permission checks happen before these run (in the loop / MCP server)."""

from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.backends.base import Backend
from agent.errors import ToolInputError
from agent.paths import jbroot
from agent.perception import blocks
from agent.perception.axtree import RenderedTree
from agent.redact import Redactor
from agent.tools import targets
from agent.tools.registry import ToolOutcome
from agent.types import Button, ImagePart, TextPart, UINode

_SHELL_OUTPUT_LIMIT = 8000
_sleep = time.sleep  # tests replace this


@dataclass
class ToolContext:
    backend: Backend
    # Returns observation parts; the loop owns perception state (refs, screenshot history).
    observe: Callable[[bool, bool], list[TextPart | ImagePart]]
    ask_user: Callable[[str], str]
    redactor: Redactor
    rendered: Optional[RenderedTree] = None
    root: Optional[UINode] = None
    memo: dict[str, object] = field(default_factory=dict)


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


# ---- block puzzles (read_grid / drag_shape) ----


@dataclass(frozen=True)
class _Board:
    x: float
    y: float
    width: float
    height: float
    rows: int
    cols: int
    pieces: Optional[tuple[float, float, float, float, float]]  # x, y, width, height, cell
    lift: float
    gain: float


def _board_args(args: dict[str, Any]) -> _Board:
    pieces = args.get("pieces")
    area: Optional[tuple[float, float, float, float, float]] = None
    if pieces is not None:
        if not isinstance(pieces, dict):
            raise ToolInputError("pieces must be an object with x, y, width, height, cell")
        px, py, pw, ph, cell = (_num(pieces, k) for k in ("x", "y", "width", "height", "cell"))
        if pw <= 0 or ph <= 0 or not 4 <= cell <= 200:
            raise ToolInputError("pieces needs a positive width/height and a cell of 4-200 points")
        area = (px, py, pw, ph, cell)
    board = _Board(
        _num(args, "x"),
        _num(args, "y"),
        _num(args, "width"),
        _num(args, "height"),
        _int_arg(args, "rows", 8, 1, 30),
        _int_arg(args, "cols", 8, 1, 30),
        area,
        float(args.get("drag_lift", 0) or 0),
        float(args.get("drag_gain", 1) or 1),
    )
    if board.width <= 0 or board.height <= 0:
        raise ToolInputError("width and height must be positive")
    if not 0.25 <= board.gain <= 4:
        raise ToolInputError("drag_gain must be between 0.25 and 4")
    return board


def _read_board(ctx: ToolContext, board: _Board) -> tuple[blocks.Grid, list[blocks.Shape]]:
    img = ctx.backend.pixels()
    grid = blocks.read_grid(
        img, board.x, board.y, board.width, board.height, board.rows, board.cols
    )
    shapes = blocks.find_shapes(img, *board.pieces) if board.pieces else []
    return grid, shapes


def _stable_read(ctx: ToolContext, board: _Board) -> tuple[blocks.Grid, list[blocks.Shape]]:
    """Read until two reads agree: clears, combos and new pieces animate for about a second."""
    last = _read_board(ctx, board)
    for _ in range(5):
        _sleep(0.35)
        now = _read_board(ctx, board)
        if now[0].cells == last[0].cells and [s.cells for s in now[1]] == [
            s.cells for s in last[1]
        ]:
            return now
        last = now
    return last


def _board_text(
    board: _Board, grid: blocks.Grid, shapes: list[blocks.Shape], list_limit: int = 24
) -> str:
    lines = [
        f"Board {grid.rows}x{grid.cols} (# filled, . empty; row 0 is the top row, "
        "col 0 the left column):",
        grid.text(),
    ]
    if board.pieces is None:
        return "\n".join(lines)
    if not shapes:
        lines.append("No pieces found in the pieces area (they may still be appearing).")
        return "\n".join(lines)
    lines.append(
        "Pieces (numbered left to right; drag_shape puts the top-left corner of a piece's "
        "box at the row/col you give, '.' inside a box is a hole):"
    )
    any_fit = False
    for s in shapes:
        spots = blocks.placements(grid, s)
        any_fit = any_fit or bool(spots)
        lines.append(f"piece {s.index}: {s.rows}x{s.cols}, {s.size} blocks")
        lines.extend("  " + row for row in s.text().splitlines())
        if not spots:
            lines.append("  fits nowhere")
        elif len(spots) <= list_limit:
            lines.append(
                f"  fits at {len(spots)} (row,col): " + " ".join(f"({r},{c})" for r, c in spots)
            )
        else:
            lines.append(f"  fits at {len(spots)} places (the board is open)")
    if not any_fit:
        lines.append("No piece fits anywhere: the game should end now.")
    return "\n".join(lines)


def read_grid(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    board = _board_args(args)
    grid, shapes = _read_board(ctx, board)
    ctx.memo.update(board=board, grid=grid, shapes=shapes)
    return ToolOutcome.text(_board_text(board, grid, shapes))


def drag_shape(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    board, grid, shapes = (ctx.memo.get(k) for k in ("board", "grid", "shapes"))
    if not (isinstance(board, _Board) and isinstance(grid, blocks.Grid)):
        raise ToolInputError("call read_grid first (with pieces) so I know the board")
    if not isinstance(shapes, list) or not shapes:
        raise ToolInputError("no pieces were found in the last read_grid; read it again")
    index = _int_arg(args, "piece", 0, 0, 99)
    if not 1 <= index <= len(shapes):
        raise ToolInputError(f"piece must be 1-{len(shapes)} (from the latest read)")
    shape: blocks.Shape = shapes[index - 1]
    row, col = _int_arg(args, "row", -1, -1, 99), _int_arg(args, "col", -1, -1, 99)
    why = blocks.fits(grid, shape, row, col)
    if why:
        return ToolOutcome.text(f"Not placed: {why}. Pick a place from the fits list.", True)

    # Aim the middle of the piece's box at the middle of its target box. Many games draw the
    # dragged piece drag_lift points above the finger and move it drag_gain times as far as the
    # finger (Block Blast on an iPhone X: 93.5 and 1.5, measured), so work back to where the
    # finger has to stop.
    gx, gy = shape.grab
    tx, ty = grid.center(row + shape.rows / 2, col + shape.cols / 2)
    fx = gx + (tx - gx) / board.gain
    fy = gy + (ty - gy + board.lift) / board.gain
    ctx.backend.swipe(gx, gy, fx, fy, 700)
    _sleep(0.6)
    new_grid, new_shapes = _stable_read(ctx, board)
    ctx.memo.update(grid=new_grid, shapes=new_shapes)

    added = [
        (r, c)
        for r in range(new_grid.rows)
        for c in range(new_grid.cols)
        if new_grid.cells[r][c] and not grid.cells[r][c]
    ]
    cleared = sum(
        1
        for r in range(grid.rows)
        for c in range(grid.cols)
        if grid.cells[r][c] and not new_grid.cells[r][c]
    )
    if not added and not cleared:
        head = (
            f"Piece {index} did not drop: the board did not change. Read the board again; "
            "if this repeats, the game may be showing something else."
        )
    else:
        want = [
            (row + r, col + c)
            for r in range(shape.rows)
            for c in range(shape.cols)
            if shape.cells[r][c]
        ]
        landed = ""
        if added and not cleared and sorted(added) != sorted(want):
            r0, c0 = min(r for r, _ in added), min(c for _, c in added)
            landed = f" It landed at about ({r0},{c0}) instead of ({row},{col})."
        head = f"Placed piece {index} at ({row},{col})." + landed
        if cleared:
            head += f" Cleared {cleared} cells."
    body = _board_text(board, new_grid, new_shapes)
    return ToolOutcome.text(head + "\n" + body)
