"""Deterministic fake device driven by fixture screens, for tests and dry runs.

A fixture directory holds one JSON file per screen::

    {
      "name": "settings",
      "foreground_app": "com.apple.Preferences",
      "screen_size": [375, 812],
      "tree": { "role": "Window", "frame": [0, 0, 375, 812], "children": [...] },
      "transitions": [
        {"action": "tap", "label": "General", "goto": "settings_general"},
        {"action": "launch_app", "bundle_id": "com.apple.Preferences", "goto": "settings"},
        {"action": "press_button", "button": "home", "goto": "home"},
        {"action": "swipe", "direction": "up", "goto": "home"}
      ]
    }

The first screen is ``start`` unless another name is given. Unmatched actions leave the screen
unchanged, which lets tests check that the loop notices "nothing happened".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.errors import BackendError
from agent.perception import png
from agent.perception.axtree import hit_test
from agent.types import Button, Capability, Image, Size, UINode

DEMO_SCREENS = Path(__file__).resolve().parent.parent / "demo" / "screens"


@dataclass
class Screen:
    name: str
    tree: UINode
    size: Size
    foreground_app: Optional[str] = None
    transitions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Screen:
        width, height = data.get("screen_size", [375, 812])
        return cls(
            name=str(data["name"]),
            tree=UINode.from_dict(data["tree"]),
            size=Size(float(width), float(height)),
            foreground_app=data.get("foreground_app"),
            transitions=list(data.get("transitions", [])),
        )


def load_screens(directory: Path) -> dict[str, Screen]:
    screens: dict[str, Screen] = {}
    for path in sorted(directory.glob("*.json")):
        screen = Screen.from_dict(json.loads(path.read_text(encoding="utf-8")))
        screens[screen.name] = screen
    if not screens:
        raise BackendError(f"no fixture screens in {directory}")
    return screens


def _swipe_direction(x1: float, y1: float, x2: float, y2: float) -> str:
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


class MockBackend:
    name = "mock"

    def __init__(self, screens: dict[str, Screen], start: str = "start") -> None:
        if start not in screens:
            raise BackendError(f"fixture has no start screen {start!r}")
        self.screens = screens
        self.current = screens[start]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.typed = ""
        self.clipboard = ""

    @classmethod
    def from_dir(cls, directory: Path, start: str = "start") -> MockBackend:
        return cls(load_screens(directory), start)

    def _record(self, op: str, *args: Any) -> None:
        self.calls.append((op, args))

    def _follow(self, action: str, **match: Any) -> None:
        for t in self.current.transitions:
            if t.get("action") != action:
                continue
            if all(t.get(k) == v for k, v in match.items() if k in t):
                target = t["goto"]
                if target not in self.screens:
                    raise BackendError(f"fixture transition to unknown screen {target!r}")
                self.current = self.screens[target]
                return

    # ---- Backend protocol ----

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(Capability)

    def screen_size(self) -> Size:
        return self.current.size

    def screenshot(self) -> Image:
        self._record("screenshot")
        # 1/5-scale blank image: tests care about plumbing, not pixels.
        w, h = int(self.current.size.width / 5), int(self.current.size.height / 5)
        return Image(png=png.solid(w, h), width=w, height=h, scale=0.2)

    def ui_tree(self) -> Optional[UINode]:
        self._record("ui_tree")
        return self.current.tree

    def tap(self, x: float, y: float) -> None:
        self._record("tap", x, y)
        node = hit_test(self.current.tree, x, y)
        if node is not None:
            self._follow("tap", label=node.label)

    def long_press(self, x: float, y: float, duration_ms: int) -> None:
        self._record("long_press", x, y, duration_ms)
        node = hit_test(self.current.tree, x, y)
        if node is not None:
            self._follow("long_press", label=node.label)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self._record("swipe", x1, y1, x2, y2, duration_ms)
        self._follow("swipe", direction=_swipe_direction(x1, y1, x2, y2))

    def type_text(self, text: str) -> None:
        self._record("type_text", text)
        self.typed += text

    def press_button(self, button: Button) -> None:
        self._record("press_button", button.value)
        self._follow("press_button", button=button.value)

    def launch_app(self, bundle_id: str) -> None:
        self._record("launch_app", bundle_id)
        self._follow("launch_app", bundle_id=bundle_id)

    def open_url(self, url: str) -> None:
        self._record("open_url", url)
        self._follow("open_url", url=url)

    def clipboard_get(self) -> str:
        self._record("clipboard_get")
        return self.clipboard

    def clipboard_set(self, text: str) -> None:
        self._record("clipboard_set", text)
        self.clipboard = text

    def foreground_app(self) -> Optional[str]:
        return self.current.foreground_app

    def close(self) -> None:
        self._record("close")
