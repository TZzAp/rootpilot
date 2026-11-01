"""Tool definitions from SPEC §3.1."""

from __future__ import annotations

from typing import Any

from agent.tools import handlers as h
from agent.tools.registry import Category, ToolDef

_TARGET: dict[str, Any] = {
    "ref": {"type": "string", "description": "Element ref from the latest UI tree, e.g. e12"},
    "x": {"type": "number", "description": "X in screen points (only if no ref)"},
    "y": {"type": "number", "description": "Y in screen points (only if no ref)"},
}


def _obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        "observe",
        "Look at the screen: a downscaled screenshot and/or the UI tree with element refs.",
        _obj(
            {
                "include_screenshot": {"type": "boolean", "default": True},
                "include_tree": {"type": "boolean", "default": True},
            }
        ),
        Category.OBSERVE,
        h.observe,
    ),
    ToolDef(
        "tap",
        "Tap an element by ref (preferred) or at x/y screen points.",
        _obj(_TARGET),
        Category.ACT,
        h.tap,
    ),
    ToolDef(
        "long_press",
        "Touch and hold an element by ref or at x/y.",
        _obj({**_TARGET, "duration_ms": {"type": "integer", "default": 800}}),
        Category.ACT,
        h.long_press,
    ),
    ToolDef(
        "swipe",
        "Swipe from (x1,y1) to (x2,y2) in screen points. Swipe up to scroll down.",
        _obj(
            {
                "x1": {"type": "number"},
                "y1": {"type": "number"},
                "x2": {"type": "number"},
                "y2": {"type": "number"},
                "duration_ms": {"type": "integer", "default": 300},
            },
            ("x1", "y1", "x2", "y2"),
        ),
        Category.ACT,
        h.swipe,
    ),
    ToolDef(
        "read_grid",
        "Read a board of coloured cells that OCR can't see (block puzzles) as text: '#' filled, "
        "'.' empty. Give the board rectangle in screen points and its size in cells. With "
        "'pieces', also read the loose pieces you can drag and list where each one fits.",
        _obj(
            {
                "x": {"type": "number", "description": "board left edge, points"},
                "y": {"type": "number", "description": "board top edge, points"},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "rows": {"type": "integer"},
                "cols": {"type": "integer"},
                "pieces": {
                    "type": "object",
                    "description": "where the draggable pieces are, and their block size",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "cell": {"type": "number", "description": "one block's size, points"},
                    },
                    "required": ["x", "y", "width", "height", "cell"],
                },
                "drag_lift": {
                    "type": "number",
                    "description": "points a dragged piece floats above the finger (game-specific)",
                },
                "drag_gain": {
                    "type": "number",
                    "description": "how far a dragged piece moves per point of finger movement "
                    "(1 = it follows the finger; game-specific)",
                },
            },
            ("x", "y", "width", "height", "rows", "cols"),
        ),
        Category.OBSERVE,
        h.read_grid,
    ),
    ToolDef(
        "drag_shape",
        "Drag piece N (numbering from the latest read_grid) onto the board so the top-left "
        "corner of its box lands on (row, col). Refuses places where it doesn't fit. Returns "
        "the new board and pieces.",
        _obj(
            {"piece": {"type": "integer"}, "row": {"type": "integer"}, "col": {"type": "integer"}},
            ("piece", "row", "col"),
        ),
        Category.ACT,
        h.drag_shape,
    ),
    ToolDef(
        "type_text",
        "Type text into the focused field, or tap the field given by ref first. "
        "Never works on password fields: ask the user to type secrets themselves.",
        _obj({"text": {"type": "string"}, "ref": _TARGET["ref"]}, ("text",)),
        Category.ACT,
        h.type_text,
    ),
    ToolDef(
        "press_button",
        "Press a hardware button. 'home' goes to the home screen.",
        _obj(
            {"button": {"type": "string", "enum": ["home", "lock", "volume_up", "volume_down"]}},
            ("button",),
        ),
        Category.ACT,
        h.press_button,
    ),
    ToolDef(
        "launch_app",
        "Open an app by bundle ID, e.g. com.apple.Preferences for Settings.",
        _obj({"bundle_id": {"type": "string"}}, ("bundle_id",)),
        Category.APP,
        h.launch_app,
    ),
    ToolDef(
        "open_url",
        "Open a URL (web page or app URL scheme).",
        _obj({"url": {"type": "string"}}, ("url",)),
        Category.APP,
        h.open_url,
    ),
    ToolDef(
        "clipboard_get",
        "Read the clipboard text. Needs the user's permission.",
        _obj({}),
        Category.CLIPBOARD_READ,
        h.clipboard_get,
    ),
    ToolDef(
        "clipboard_set",
        "Put text on the clipboard.",
        _obj({"text": {"type": "string"}}, ("text",)),
        Category.ACT,
        h.clipboard_set,
    ),
    ToolDef(
        "wait",
        "Wait 0.1–10 seconds for the screen to change, then observe.",
        _obj({"seconds": {"type": "number"}}, ("seconds",)),
        Category.OBSERVE,
        h.wait,
    ),
    ToolDef(
        "shell",
        "Run a shell command on the device as the current user. Needs the user's permission; "
        "prefer UI actions.",
        _obj(
            {"command": {"type": "string"}, "timeout_s": {"type": "integer", "default": 20}},
            ("command",),
        ),
        Category.SHELL,
        h.shell,
    ),
    ToolDef(
        "ask_user",
        "Ask the user a question and wait for the answer.",
        _obj({"question": {"type": "string"}}, ("question",)),
        Category.META,
        h.ask_user,
    ),
    ToolDef(
        "done",
        "Finish the task. Put the final answer for the user in 'answer'.",
        _obj({"answer": {"type": "string"}, "success": {"type": "boolean"}}, ("answer", "success")),
        Category.META,
        h.done,
    ),
)
