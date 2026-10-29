"""Resolve a tool's target (``ref`` or ``x``/``y``) to a point and, if possible, an element.

Shared by the handlers (to act) and the permission engine (to judge what is being touched),
so both always agree on the target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agent.errors import ToolInputError
from agent.perception.axtree import RenderedTree, hit_test
from agent.types import UINode


@dataclass(frozen=True)
class Target:
    x: float
    y: float
    node: Optional[UINode]


def resolve(
    args: dict[str, Any], rendered: Optional[RenderedTree], root: Optional[UINode]
) -> Target:
    ref = args.get("ref")
    if ref is not None:
        if rendered is None:
            raise ToolInputError("no UI tree available; use x/y coordinates")
        node = rendered.resolve(str(ref))
        if node.frame is None:
            raise ToolInputError(f"element {ref} has no position")
        x, y = node.frame.center
        return Target(x, y, node)
    if "x" in args and "y" in args:
        try:
            x, y = float(args["x"]), float(args["y"])
        except (TypeError, ValueError) as exc:
            raise ToolInputError("x and y must be numbers") from exc
        return Target(x, y, hit_test(root, x, y) if root is not None else None)
    raise ToolInputError("give either ref or both x and y")
