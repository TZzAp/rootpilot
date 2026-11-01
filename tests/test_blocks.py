"""Block-puzzle perception (read_grid / find_shapes) and the read_grid / drag_shape tools."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agent.backends.mock import MockBackend
from agent.errors import ToolInputError
from agent.perception import blocks
from agent.perception.png import RGBImage
from agent.redact import Redactor
from agent.tools import handlers
from agent.tools.handlers import ToolContext
from agent.types import Size, UINode

BG = (65, 82, 144)  # Block Blast's screen background
EMPTY = (34, 40, 74)  # an empty board cell
COLOURS = [(140, 90, 210), (230, 180, 70), (100, 190, 230), (100, 190, 80), (230, 120, 60)]
BOARD = (22.0, 191.0, 328.0, 328.0)  # x, y, w, h: 8 cells of 41 points
TRAY = (15.0, 570.0, 345.0, 110.0)
CELL = 18.0


class Canvas:
    def __init__(self, width: int = 375, height: int = 812) -> None:
        self.width, self.height = width, height
        self.px = bytearray(bytes(BG) * (width * height))

    def rect(self, x: float, y: float, w: float, h: float, rgb: tuple[int, int, int]) -> None:
        for yy in range(int(y), int(y + h)):
            for xx in range(int(x), int(x + w)):
                i = (yy * self.width + xx) * 3
                self.px[i : i + 3] = bytes(rgb)

    def board(self, rows: list[str]) -> Canvas:
        x0, y0, w, _ = BOARD
        cell = w / 8
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                rgb = COLOURS[(r + c) % len(COLOURS)] if ch == "#" else EMPTY
                self.rect(x0 + c * cell + 1, y0 + r * cell + 1, cell - 2, cell - 2, rgb)
        return self

    def piece(self, x: float, y: float, rows: list[str], rgb: tuple[int, int, int]) -> Canvas:
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                if ch == "#":
                    self.rect(x + c * CELL, y + r * CELL, CELL - 1, CELL - 1, rgb)
        return self

    def image(self) -> RGBImage:
        return RGBImage(self.width, self.height, bytes(self.px))


START = [
    "#......#",
    "#....#.#",
    "#..#####",
    "#..####.",
    "...#.###",
    ".....###",
    ".......#",
    "...##..#",
]


def test_read_grid_matches_the_board() -> None:
    img = Canvas().board(START).image()
    grid = blocks.read_grid(img, *BOARD, 8, 8)
    assert grid.text().splitlines()[1:] == [f"{r:2d} {line}" for r, line in enumerate(START)]
    assert grid.full_lines() == ([], [])


def test_read_grid_all_one_class() -> None:
    empty = blocks.read_grid(Canvas().board(["." * 8] * 8).image(), *BOARD, 8, 8)
    assert not any(v for row in empty.cells for v in row)
    full = blocks.read_grid(Canvas().board(["#" * 8] * 8).image(), *BOARD, 8, 8)
    assert all(v for row in full.cells for v in row)
    assert full.full_lines() == (list(range(8)), list(range(8)))


def test_find_shapes_reads_pieces_left_to_right_and_ignores_the_edge_glow() -> None:
    canvas = Canvas().board(START)
    canvas.piece(40, 595, ["#.", "##", "#."], COLOURS[4])  # a T
    canvas.piece(150, 612, ["#####"], COLOURS[0])  # a bar
    canvas.piece(290, 603, ["##", "##"], COLOURS[3])  # a square
    canvas.rect(15, 570, 6, 110, (255, 150, 60))  # the overlay's glow along the edge
    shapes = blocks.find_shapes(canvas.image(), *TRAY, CELL)
    assert [s.text() for s in shapes] == ["#.\n##\n#.", "#####", "##\n##"]
    assert [s.index for s in shapes] == [1, 2, 3]
    t = shapes[0]
    assert (t.rows, t.cols, t.size) == (3, 2, 4)
    assert abs(t.grab[0] - (40 + CELL)) < 2 and abs(t.grab[1] - (595 + 1.5 * CELL)) < 2


def test_fits_and_placements() -> None:
    grid = blocks.read_grid(Canvas().board(START).image(), *BOARD, 8, 8)
    bar = blocks.Shape(1, [[True] * 5], (0.0, 0.0))
    assert blocks.placements(grid, bar) == [(0, 1), (0, 2), (5, 0), (6, 0), (6, 1), (6, 2)]
    assert "stick out" in blocks.fits(grid, bar, 0, 4)
    assert "already filled" in blocks.fits(grid, bar, 1, 1)
    t = blocks.Shape(2, [[True, False], [True, True], [True, False]], (0.0, 0.0))
    assert not blocks.fits(grid, t, 0, 2)  # its hole may sit over the filled (2,3)
    assert "already filled" in blocks.fits(grid, t, 4, 4)


def _ctx(backend: MockBackend) -> ToolContext:
    return ToolContext(backend, lambda s, t: [], lambda q: "", Redactor())


def _backend(*frames: RGBImage) -> MockBackend:
    from agent.backends.mock import Screen

    backend = MockBackend({"start": Screen("start", UINode("app"), Size(375, 812))})
    backend.frames = list(frames)
    return backend


BOARD_ARGS: dict[str, Any] = {
    "x": BOARD[0],
    "y": BOARD[1],
    "width": BOARD[2],
    "height": BOARD[3],
    "rows": 8,
    "cols": 8,
    "pieces": {"x": TRAY[0], "y": TRAY[1], "width": TRAY[2], "height": TRAY[3], "cell": CELL},
    "drag_lift": 60,
}


def test_read_grid_then_drag_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "_sleep", lambda s: None)
    before = Canvas().board(START).piece(150, 612, ["#####"], COLOURS[0]).image()
    after_rows = list(START)
    after_rows[6] = "#####..#"
    after = Canvas().board(after_rows).image()
    backend = _backend(before, after)
    ctx = _ctx(backend)

    out = handlers.read_grid(ctx, dict(BOARD_ARGS))
    text = out.content[0].text  # type: ignore[union-attr]
    assert " 0 #......#" in text and "piece 1: 1x5, 5 blocks" in text
    assert "fits at 6 (row,col): (0,1) (0,2) (5,0) (6,0) (6,1) (6,2)" in text

    refused = handlers.drag_shape(ctx, {"piece": 1, "row": 1, "col": 1})
    assert refused.is_error and "already filled" in refused.content[0].text  # type: ignore[union-attr]
    assert not [c for c in backend.calls if c[0] == "swipe"]

    out = handlers.drag_shape(ctx, {"piece": 1, "row": 6, "col": 0})
    assert not out.is_error
    text = out.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Placed piece 1 at (6,0).") and "landed" not in text
    assert " 6 #####..#" in text and "No pieces found" in text
    (swipe,) = [c for c in backend.calls if c[0] == "swipe"]
    x1, y1, x2, y2, ms = swipe[1]
    cell = BOARD[2] / 8
    assert x1 == pytest.approx(150 + 2.5 * CELL, abs=1.5)  # middle of the bar in the tray
    assert y1 == pytest.approx(612 + 0.5 * CELL, abs=1.5)
    assert x2 == pytest.approx(BOARD[0] + 2.5 * cell)  # middle of cols 0-4
    assert y2 == pytest.approx(BOARD[1] + 6.5 * cell + 60)  # row 6, plus the lift
    assert ms == 700


def test_drag_shape_reports_where_a_piece_really_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "_sleep", lambda s: None)
    before = Canvas().board(START).piece(150, 612, ["#####"], COLOURS[0]).image()
    rows = list(START)
    rows[5] = "#######"[:5] + "###"  # landed one row higher than asked
    backend = _backend(before, Canvas().board(rows).image())
    ctx = _ctx(backend)
    handlers.read_grid(ctx, dict(BOARD_ARGS))
    text = handlers.drag_shape(ctx, {"piece": 1, "row": 6, "col": 0}).content[0].text  # type: ignore[union-attr]
    assert "landed at about (5,0) instead of (6,0)" in text


def test_drag_shape_needs_a_read_first() -> None:
    with pytest.raises(ToolInputError, match="read_grid first"):
        handlers.drag_shape(_ctx(_backend()), {"piece": 1, "row": 0, "col": 0})


def test_hud_pixels_round_trip(tmp_path: Path) -> None:
    from agent.backends.hud_ocr import HudPixels

    folder = tmp_path / "rp"

    def overlay(name: str) -> None:  # stands in for SpringBoard
        def answer() -> None:
            req = json.loads((folder / "pixels.request").read_text())
            (folder / "pixels.rgb").write_bytes(bytes([1, 2, 3]) * 6)
            (folder / "pixels.result.json").write_text(
                json.dumps({"id": req["id"], "ok": True, "w": 3, "h": 2, "file": "/elsewhere"})
            )

        assert name == "com.rootpilot.hud.pixels"
        threading.Thread(target=answer).start()

    img = HudPixels(folder=folder, notify=overlay, timeout=5).read()
    assert (img.width, img.height, img.pixels[:3]) == (3, 2, bytes([1, 2, 3]))
    assert not (folder / "pixels.rgb").exists()  # a picture of the screen doesn't stay around


def test_drag_shape_works_back_from_lift_and_gain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block Blast moves a piece 1.5x as far as the finger, 93.5 pt above it (measured)."""
    monkeypatch.setattr(handlers, "_sleep", lambda s: None)
    before = Canvas().board(START).piece(150, 612, ["#####"], COLOURS[0]).image()
    backend = _backend(before)
    ctx = _ctx(backend)
    handlers.read_grid(ctx, {**BOARD_ARGS, "drag_lift": 93.5, "drag_gain": 1.5})
    handlers.drag_shape(ctx, {"piece": 1, "row": 0, "col": 1})
    (swipe,) = [c for c in backend.calls if c[0] == "swipe"]
    gx, gy, fx, fy, _ = swipe[1]
    cell = BOARD[2] / 8
    tx, ty = BOARD[0] + 3.5 * cell, BOARD[1] + 0.5 * cell
    # Where the game would draw the piece's middle when the finger lifts:
    assert gx + 1.5 * (fx - gx) == pytest.approx(tx)
    assert gy - 93.5 + 1.5 * (fy - gy) == pytest.approx(ty)
    with pytest.raises(ToolInputError, match="drag_gain"):
        handlers.read_grid(ctx, {**BOARD_ARGS, "drag_gain": 9})
