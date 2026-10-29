"""Render accessibility trees to compact text with element refs (SPEC §3.3).

Example line: ``e12 Button "General" @(201,406)``. Refs are only valid for the observation that
produced them; the loop swaps in a fresh ``RenderedTree`` after every observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.errors import StaleRefError
from agent.types import UINode

INTERACTIVE_ROLES = frozenset(
    {
        "Button",
        "Cell",
        "Link",
        "Switch",
        "Slider",
        "TextField",
        "SecureTextField",
        "SearchField",
        "TextView",
        "Tab",
        "Key",
        "Icon",
        "MenuItem",
        "SegmentedControl",
        "Picker",
    }
)

_MAX_TEXT = 80


@dataclass
class RenderedTree:
    text: str
    refs: dict[str, UINode] = field(default_factory=dict)
    omitted: int = 0

    def resolve(self, ref: str) -> UINode:
        node = self.refs.get(ref)
        if node is None:
            raise StaleRefError(f"unknown element ref {ref!r}; observe again and use a current ref")
        return node


def _quote(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 1] + "…"
    return '"' + text.replace('"', '\\"') + '"'


def is_meaningful(node: UINode) -> bool:
    if not node.visible or node.frame is None:
        return False
    return bool(node.label or node.value or node.secure or node.role in INTERACTIVE_ROLES)


def describe(ref: str, node: UINode) -> str:
    role = "SecureTextField" if node.secure else node.role
    parts = [ref, role]
    if node.label:
        parts.append(_quote(node.label))
    if node.value and not node.secure:
        parts.append("= " + _quote(node.value))
    if node.frame is not None:
        cx, cy = node.frame.center
        parts.append(f"@({round(cx)},{round(cy)})")
    flags = [
        name
        for name, on in (
            ("secure", node.secure),
            ("disabled", not node.enabled),
            ("focused", node.focused),
        )
        if on
    ]
    if flags:
        parts.append("[" + ",".join(flags) + "]")
    return " ".join(parts)


def render(root: UINode, max_elements: int = 250) -> RenderedTree:
    lines: list[str] = []
    refs: dict[str, UINode] = {}
    omitted = 0

    def visit(node: UINode, depth: int) -> None:
        nonlocal omitted
        if not node.visible:
            return
        child_depth = depth
        if is_meaningful(node):
            if len(refs) < max_elements:
                ref = f"e{len(refs) + 1}"
                refs[ref] = node
                lines.append("  " * depth + describe(ref, node))
            else:
                omitted += 1
            child_depth = depth + 1
        for child in node.children:
            visit(child, child_depth)

    visit(root, 0)
    if omitted:
        lines.append(f"… {omitted} more elements")
    return RenderedTree("\n".join(lines), refs, omitted)


def hit_test(root: UINode, x: float, y: float) -> Optional[UINode]:
    """Deepest meaningful visible element containing the point, if any."""
    best: Optional[UINode] = None

    def visit(node: UINode) -> None:
        nonlocal best
        if not node.visible:
            return
        if node.frame is not None and node.frame.contains(x, y) and is_meaningful(node):
            best = node
        for child in node.children:
            visit(child)

    visit(root)
    return best


def secure_focused(root: UINode) -> bool:
    return any(n.secure and n.focused for n in root.walk())
