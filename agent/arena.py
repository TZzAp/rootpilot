"""RootPilot Arena: short, seeded mini-games for benchmarking an agent on a phone.

Chess costs hundreds of model calls per game. The arena measures the same abilities —
reading the screen, tapping accurately, sequencing, scrolling, typing, simple reasoning — in a
few minutes and a few cents, with an exact score. Every game is a page served by the control
server on loopback (no outside sites), generated from a seed so two phones get identical
puzzles, and it posts its score after every round.

Labels avoid the permission engine's irreversible words (Submit, Send, Get…) so a benchmark
run never waits on a confirmation prompt.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from agent.loop import RunResult


@dataclass(frozen=True)
class Game:
    id: str
    title: str
    skill: str
    rounds: int
    max_steps: int
    max_seconds: int
    native: bool = False  # uses an iOS app instead of an arena page


GAMES = {
    g.id: g
    for g in (
        Game("word-hunt", "Word hunt", "read the screen and tap the right word", 4, 30, 240),
        Game("math", "Mental math", "work out a product and enter it on a keypad", 2, 30, 240),
        Game("order", "Number order", "tap scattered numbers in ascending order", 1, 30, 240),
        Game("scroll", "Scroll and find", "scroll a long list to find an item", 1, 25, 240),
        Game("type", "Typing", "type a word into a text field", 2, 25, 240),
        Game(
            "calculator",
            "Calculator app",
            "use a real iOS app to compute a product",
            1,
            25,
            240,
            True,
        ),
    )
}

SUITES = {
    "quick": ("word-hunt", "math", "order", "type"),
    "full": ("word-hunt", "math", "order", "scroll", "type", "calculator"),
}


def calculator_operands(seed: int) -> tuple[int, int]:
    """Same mulberry-free arithmetic on every device: deterministic from the seed."""
    return 13 + (seed * 37) % 80, 3 + (seed * 11) % 7


def task_for(game: Game, url: str, seed: int) -> str:
    if game.native:
        a, b = calculator_operands(seed)
        return (
            f"Use the iOS Calculator app (bundle id com.apple.calculator) to calculate {a} × {b}. "
            "Clear it first with AC or C if it shows a previous value. Read the result from the "
            "calculator display, then call done with the result as a number."
        )
    return (
        f"Open {url} with open_url. It is a short test game in Safari made for you: "
        f"{game.title.lower()}, {game.skill}. Read the instructions on the page and follow them "
        "exactly, using taps (and typing only where the page asks for it). Work quickly but "
        "carefully: wrong taps count against you. Only interact with this page. When the page "
        "shows DONE with a score, the game is over: call done with that score."
    )


@dataclass
class Session:
    game: str
    created: float
    result: Optional[dict[str, Any]] = None


class ArenaSessions:
    """Pages may only report results for sessions the benchmark started."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, game: str) -> str:
        sid = secrets.token_hex(8)
        with self._lock:
            self._sessions[sid] = Session(game, time.time())
        return sid

    def known(self, sid: str) -> bool:
        with self._lock:
            return sid in self._sessions

    def record(self, sid: str, data: dict[str, Any]) -> bool:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None or str(data.get("game")) != session.game:
                return False
            session.result = {
                k: data.get(k) for k in ("game", "correct", "total", "mistakes", "ms", "finished")
            }
            return True

    def result(self, sid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(sid)
            return dict(session.result) if session and session.result else None


def _number_in(text: str) -> Optional[int]:
    numbers = re.findall(r"-?\d[\d,]*", text)
    if not numbers:
        return None
    try:
        return int(numbers[-1].replace(",", ""))
    except ValueError:
        return None


def score_game(
    game: Game,
    seed: int,
    page: Optional[dict[str, Any]],
    run: RunResult,
    wall_seconds: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "game": game.id,
        "title": game.title,
        "skill": game.skill,
        "steps": run.steps,
        "input_tokens": run.usage.input_tokens,
        "output_tokens": run.usage.output_tokens,
        "seconds": round(wall_seconds, 1),
        "reason": run.reason,
    }
    if game.native:
        a, b = calculator_operands(seed)
        ok = _number_in(run.answer) == a * b
        row.update(correct=int(ok), total=1, mistakes=0, finished=run.reason == "done")
        return row
    page = page or {}
    row.update(
        correct=int(page.get("correct") or 0),
        total=int(page.get("total") or game.rounds),
        mistakes=int(page.get("mistakes") or 0),
        finished=bool(page.get("finished")),
    )
    return row


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(r["correct"] for r in rows)
    total = sum(r["total"] for r in rows) or 1
    return {
        "score": round(100 * correct / total, 1),
        "correct": correct,
        "total": total,
        "mistakes": sum(r["mistakes"] for r in rows),
        "finished": sum(1 for r in rows if r["finished"]),
        "games": len(rows),
        "seconds": round(sum(r["seconds"] for r in rows), 1),
        "steps": sum(r["steps"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
    }
