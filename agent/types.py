"""Core data types: geometry, device state, and the provider-neutral message format."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass(frozen=True)
class Size:
    width: float
    height: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class Image:
    """A PNG screenshot. ``scale`` is image pixels per screen point."""

    png: bytes
    width: int
    height: int
    scale: float = 1.0


class Button(str, enum.Enum):
    HOME = "home"
    LOCK = "lock"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"


class Capability(str, enum.Enum):
    SCREENSHOT = "screenshot"
    UI_TREE = "ui_tree"
    TOUCH = "touch"
    TEXT_INPUT = "text_input"
    BUTTONS = "buttons"
    LAUNCH_APP = "launch_app"
    OPEN_URL = "open_url"
    CLIPBOARD = "clipboard"
    FOREGROUND_APP = "foreground_app"


@dataclass
class UINode:
    """One accessibility element. ``frame`` is in screen points."""

    role: str
    label: str = ""
    value: str = ""
    identifier: str = ""
    frame: Optional[Rect] = None
    visible: bool = True
    enabled: bool = True
    secure: bool = False
    focused: bool = False
    children: list[UINode] = field(default_factory=list)

    def walk(self) -> list[UINode]:
        out: list[UINode] = [self]
        for child in self.children:
            out.extend(child.walk())
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UINode:
        frame = data.get("frame")
        return cls(
            role=str(data.get("role", "Other")),
            label=str(data.get("label", "")),
            value=str(data.get("value", "")),
            identifier=str(data.get("identifier", "")),
            frame=Rect(*(float(v) for v in frame)) if frame else None,
            visible=bool(data.get("visible", True)),
            enabled=bool(data.get("enabled", True)),
            secure=bool(data.get("secure", False)),
            focused=bool(data.get("focused", False)),
            children=[cls.from_dict(c) for c in data.get("children", [])],
        )


# ---- provider-neutral message format -------------------------------------------------


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: list[Union[TextPart, ImagePart]]
    is_error: bool = False


Part = Union[TextPart, ImagePart, ToolCall, ToolResult]


@dataclass
class Message:
    role: str  # "user" or "assistant"
    parts: list[Part]
    # The provider's own wire content for this message (e.g. Anthropic thinking blocks with
    # signatures). Providers replay it verbatim when it came from them: histories must stay
    # append-only and byte-identical, or replayed thinking blocks are rejected.
    raw: Any = None
    raw_provider: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: str = ""
    raw: Any = None  # provider wire content, stored on the assistant Message
    refusal: str = ""  # set when the model declined (e.g. a safety classifier)
