"""Tool registry: each tool is defined once and shared by the agent loop and MCP (SPEC §3.1)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from agent.types import ImagePart, TextPart, ToolSpec

if TYPE_CHECKING:
    from agent.tools.handlers import ToolContext


class Category(str, enum.Enum):
    OBSERVE = "observe"
    ACT = "act"
    APP = "app"
    CLIPBOARD_READ = "clipboard_read"
    SHELL = "shell"
    META = "meta"


@dataclass
class ToolOutcome:
    content: list[TextPart | ImagePart] = field(default_factory=list)
    is_error: bool = False
    # Set by ``done``: (answer, success). Ends the run.
    finished: Optional[tuple[str, bool]] = None

    @classmethod
    def text(cls, message: str, is_error: bool = False) -> ToolOutcome:
        return cls([TextPart(message)], is_error=is_error)


Handler = Callable[["ToolContext", dict[str, Any]], ToolOutcome]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    category: Category
    handler: Handler

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)

    @property
    def changes_screen(self) -> bool:
        """Whether the loop should re-observe and verify after this tool runs."""
        return self.category in (Category.ACT, Category.APP) and self.name != "clipboard_set"


class Registry:
    def __init__(self, tools: list[ToolDef]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def __iter__(self) -> Any:
        return iter(self._tools.values())

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]


def default_registry() -> Registry:
    from agent.tools.specs import TOOLS

    return Registry(list(TOOLS))
