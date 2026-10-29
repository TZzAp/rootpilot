"""Permission engine: every tool call is allowed, needs confirmation, or is denied (SPEC §3.4).

Rules combine by severity, so a deny from any rule wins over an ask, and an ask wins over an
allow. Category defaults can be loosened in config or per run; the escalations below
(sensitive apps, irreversible taps, unusual URL schemes, secure fields, shell deny-list) can't.
"""

from __future__ import annotations

import enum
import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from agent.config import Config
from agent.errors import ConfigError, ToolInputError
from agent.perception.axtree import RenderedTree, secure_focused
from agent.tools import targets
from agent.tools.registry import Category, ToolDef
from agent.types import UINode


class Verdict(int, enum.Enum):
    ALLOW = 0
    ASK = 1
    DENY = 2


class Rule(str, enum.Enum):
    DEFAULT = "default"
    SENSITIVE_APP = "sensitive_app"
    IRREVERSIBLE = "irreversible"
    URL_SCHEME = "url_scheme"
    SECURE_FIELD = "secure_field"
    SHELL_DENY_LIST = "shell_deny_list"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    rule: Rule = Rule.DEFAULT

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


ALLOW = Decision(Verdict.ALLOW, "allowed")

DEFAULTS: dict[Category, Verdict] = {
    Category.OBSERVE: Verdict.ALLOW,
    Category.META: Verdict.ALLOW,
    Category.ACT: Verdict.ALLOW,
    Category.APP: Verdict.ALLOW,
    Category.CLIPBOARD_READ: Verdict.ASK,
    Category.SHELL: Verdict.ASK,
}

SENSITIVE_APPS = (
    "com.apple.Passbook",
    "com.apple.Passwords",
    "com.apple.Health",
    "com.apple.AppStore",
    "com.apple.MobileSMS",
    "com.apple.mobilemail",
    "com.apple.mobilephone",
    "com.apple.facetime",
    "com.apple.MobileStore",
    "com.apple.Bridge",
)

# Settings is only sensitive on these pages (matched against the page title).
SENSITIVE_SETTINGS_PAGES = re.compile(
    r"(?i)\b(passcode|face id|touch id|apple id|password|reset|erase|screen time|"
    r"emergency sos|wallet|apple pay|transfer)\b"
)

IRREVERSIBLE_WORDS = (
    "send",
    "pay",
    "buy",
    "purchase",
    "subscribe",
    "delete",
    "erase",
    "remove",
    "reset",
    "transfer",
    "post",
    "publish",
    "confirm",
    "sign out",
    "log out",
    "install",
    "uninstall",
    "get",
    "order",
    "submit",
    "call",
    "accept",
    "allow",
)

SAFE_URL_SCHEMES = ("http", "https")

_ROOTS = r"(/|/\*|/var|/var/jb|/private|/private/var|/System|/usr|/Applications|~|\$HOME|\$JBROOT)"
SHELL_DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        rf"\brm\s+(-\S*\s+)*-\S*[rRf]\S*\s+(-\S+\s+)*{_ROOTS}/?(\s|$|;|&|\|)",
        "recursive delete of a system root",
    ),
    (r"\b(dd|mkfs\S*|newfs\S*|nvram|fdisk|gpt|diskutil|apfs_\w+)\b", "disk or firmware tool"),
    (r"\b(passwd|chpass|pwpolicy|dscl)\b", "account/password change"),
    (r"(master\.passwd|/etc/passwd|sudoers)", "edits system accounts"),
    (r"\blaunchctl\s+(unload|bootout|remove|disable|kill)\b", "stops system services"),
    (
        r"\b(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b",
        "pipes a download into a shell",
    ),
    (
        r"(keychain[-_]?dumper|keychaineditor|dump-keychain|keychain-2\.db|/Keychains\b)",
        "reads the keychain",
    ),
    (r"\b(nc|ncat|netcat|socat|telnet)\b.*\b6000\b", "talks to the ZXTouch socket directly"),
    (r"rootpilotd(\.sock)?", "talks to the RootPilot helper directly"),
    (r"\bsudo\b|\bsu\s+(-|root)\b|\bsu\s*$", "privilege escalation"),
    (
        r"(/var/jb|\$JBROOT)?/?\.(installed|procursus_strapped)",
        "touches jailbreak bootstrap markers",
    ),
)
REBOOT_PATTERN = (
    r"\b(reboot|halt|shutdown|ldrestart|userspace-reboot|launchctl\s+reboot)\b",
    "reboots the device",
)


def _normalise(command: str) -> str:
    return " ".join(command.replace("\\\n", " ").split())


@dataclass
class PermissionContext:
    foreground_app: Optional[str] = None
    rendered: Optional[RenderedTree] = None
    root: Optional[UINode] = None


@dataclass
class PermissionEngine:
    config: Config
    session_allowed: set[Category] = field(default_factory=set)

    def __post_init__(self) -> None:
        for category, verdict in self.config.category_defaults.items():
            if (
                category not in {c.value for c in Category}
                or verdict.upper() not in Verdict.__members__
            ):
                raise ConfigError(
                    f"category_defaults: {category!r}: {verdict!r} must be allow, ask or deny "
                    f"for one of {', '.join(c.value for c in Category)}"
                )
        self._sensitive = tuple(SENSITIVE_APPS) + tuple(self.config.sensitive_apps)
        words = tuple(IRREVERSIBLE_WORDS) + tuple(self.config.irreversible_words)
        self._irreversible = re.compile(
            r"(?i)(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])"
        )
        patterns = list(SHELL_DENY_PATTERNS)
        if not self.config.shell_allow_reboot:
            patterns.append(REBOOT_PATTERN)
        self._shell_deny = [(re.compile(p, re.IGNORECASE), why) for p, why in patterns]

    # ---- public API ----

    def allow_for_session(self, category: Category) -> bool:
        """Remember 'always allow' for a category. Shell can never be session-allowed."""
        if category is Category.SHELL:
            return False
        self.session_allowed.add(category)
        return True

    def check(self, tool: ToolDef, args: dict[str, Any], ctx: PermissionContext) -> Decision:
        decisions = [self._default(tool.category)]
        decisions += self._escalations(tool, args, ctx)
        return max(decisions, key=lambda d: d.verdict.value)

    def is_sensitive_app(self, bundle_id: Optional[str], ctx: PermissionContext) -> bool:
        if not bundle_id:
            return False
        if bundle_id == "com.apple.Preferences":
            title = self._page_title(ctx)
            return bool(title and SENSITIVE_SETTINGS_PAGES.search(title))
        return any(fnmatch.fnmatchcase(bundle_id, pattern) for pattern in self._sensitive)

    def shell_denied(self, command: str) -> Optional[str]:
        normalised = _normalise(command)
        for pattern, why in self._shell_deny:
            if pattern.search(normalised):
                return why
        return None

    # ---- rules ----

    def _default(self, category: Category) -> Decision:
        override = self.config.category_defaults.get(category.value)
        verdict = Verdict[override.upper()] if override else DEFAULTS[category]
        if verdict is Verdict.ASK and category in self.session_allowed:
            verdict = Verdict.ALLOW
        if verdict is Verdict.ALLOW:
            return ALLOW
        return Decision(verdict, f"{category.value} actions need confirmation by default")

    def _escalations(
        self, tool: ToolDef, args: dict[str, Any], ctx: PermissionContext
    ) -> list[Decision]:
        out: list[Decision] = []
        name = tool.name

        if name == "shell":
            why = self.shell_denied(str(args.get("command", "")))
            if why:
                out.append(
                    Decision(Verdict.DENY, f"blocked shell command: {why}", Rule.SHELL_DENY_LIST)
                )

        if tool.category in (Category.ACT, Category.APP):
            app = ctx.foreground_app
            if self.is_sensitive_app(app, ctx):
                out.append(Decision(Verdict.ASK, f"{app} is a sensitive app", Rule.SENSITIVE_APP))
            if name == "launch_app":
                target_app = str(args.get("bundle_id", ""))
                if any(fnmatch.fnmatchcase(target_app, p) for p in self._sensitive):
                    out.append(
                        Decision(
                            Verdict.ASK, f"opening sensitive app {target_app}", Rule.SENSITIVE_APP
                        )
                    )

        if name == "open_url":
            scheme = urlsplit(str(args.get("url", ""))).scheme.lower()
            if scheme not in SAFE_URL_SCHEMES:
                out.append(
                    Decision(
                        Verdict.ASK, f"opening a {scheme or 'scheme-less'}: URL", Rule.URL_SCHEME
                    )
                )

        if name in ("tap", "long_press"):
            label = self._target_label(args, ctx)
            if label:
                match = self._irreversible.search(label)
                if match:
                    out.append(
                        Decision(
                            Verdict.ASK,
                            f'"{label}" looks irreversible ({match.group(1).lower()})',
                            Rule.IRREVERSIBLE,
                        )
                    )

        if name == "type_text" and self._typing_into_secure_field(args, ctx):
            out.append(
                Decision(
                    Verdict.DENY,
                    "RootPilot never types into password fields; ask the user to type it",
                    Rule.SECURE_FIELD,
                )
            )
        return out

    # ---- helpers ----

    @staticmethod
    def _page_title(ctx: PermissionContext) -> str:
        if ctx.root is None:
            return ""
        for node in ctx.root.walk():
            if node.role == "NavigationBar" and node.label:
                return node.label
        # OCR trees have no roles: use text near the top of the screen.
        top = [
            n.label
            for n in ctx.root.walk()
            if n.label and n.frame is not None and n.frame.y < 140 and n is not ctx.root
        ]
        return " ".join(top)

    @staticmethod
    def _target_label(args: dict[str, Any], ctx: PermissionContext) -> str:
        try:
            node = targets.resolve(args, ctx.rendered, ctx.root).node
        except ToolInputError:
            return ""
        if node is None:
            return ""
        return node.label or node.value

    @staticmethod
    def _typing_into_secure_field(args: dict[str, Any], ctx: PermissionContext) -> bool:
        if args.get("ref") is not None:
            try:
                node = targets.resolve(args, ctx.rendered, ctx.root).node
            except ToolInputError:
                return False
            return bool(node and node.secure)
        return ctx.root is not None and secure_focused(ctx.root)
