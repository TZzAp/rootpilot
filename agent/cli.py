"""Command line: ``rootpilot doctor``, ``rootpilot run "<task>"`` and the interactive REPL."""

from __future__ import annotations

import argparse
import platform
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from agent import __version__
from agent.audit import AuditLog
from agent.backends.base import Backend
from agent.config import Config, default_config_path, load_config
from agent.errors import RootPilotError
from agent.loop import ALWAYS, NO, YES, Agent, RunResult
from agent.paths import ensure_private_dir, jbroot, state_dir, stop_file
from agent.permissions import Decision, PermissionEngine, Rule
from agent.providers import PROVIDERS, make_provider
from agent.redact import MASK, Redactor
from agent.tools.registry import Category, ToolDef, default_registry

OK, WARN, FAIL = "ok", "warn", "FAIL"


def make_backend(cfg: Config, fixture: Optional[str]) -> Backend:
    if cfg.backend == "mock":
        from agent.backends.mock import DEMO_SCREENS, MockBackend

        return MockBackend.from_dir(Path(fixture) if fixture else DEMO_SCREENS)
    if cfg.backend == "zxtouch":
        from agent.backends.zxtouch import ZXTouchBackend

        return ZXTouchBackend(cfg.zxtouch_host, cfg.zxtouch_port)
    raise RootPilotError(f"unknown backend {cfg.backend!r}; choose mock or zxtouch")


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "backend": args.backend,
        "provider": args.provider,
        "model": args.model,
        "max_steps": getattr(args, "max_steps", None),
        "save_screens": True if getattr(args, "save_screens", False) else None,
    }


def _load(args: argparse.Namespace) -> Config:
    return load_config(Path(args.config) if args.config else None, overrides=_overrides(args))


# ---- doctor ----------------------------------------------------------------------------


def doctor(args: argparse.Namespace, out: TextIO) -> int:
    checks: list[tuple[str, str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append((name, status, detail))

    py = sys.version_info
    add("python", OK if py >= (3, 9) else FAIL, f"{platform.python_version()} ({sys.executable})")
    add("platform", OK, f"{platform.system()} {platform.machine()}; jailbreak root {jbroot()}")

    cfg_path = Path(args.config) if args.config else default_config_path()
    if cfg_path.exists():
        mode = stat.S_IMODE(cfg_path.stat().st_mode)
        add("config file", OK if not mode & 0o077 else WARN, f"{cfg_path} (mode {oct(mode)})")
    else:
        add("config file", OK, f"none at {cfg_path} (using defaults and environment)")

    try:
        cfg = _load(args)
    except RootPilotError as exc:
        add("config", FAIL, str(exc))
        return _print_checks(checks, out)

    key = {
        "anthropic": cfg.anthropic_api_key,
        "openai": cfg.openai_api_key,
        "deepseek": cfg.deepseek_api_key,
        "compat": cfg.compat_api_key or "(not needed)",
    }.get(cfg.provider, "")
    try:
        provider = make_provider(cfg, Redactor(cfg.secrets()))
        add(
            "provider", OK, f"{cfg.provider}, model {provider.model}, key {MASK if key else 'none'}"
        )
    except (RootPilotError, NotImplementedError) as exc:
        add("provider", FAIL, str(exc))

    try:
        backend = make_backend(cfg, args.fixture)
        size = backend.screen_size()
        caps = ", ".join(sorted(c.value for c in backend.capabilities()))
        add("backend", OK, f"{backend.name}: {size.width:.0f}x{size.height:.0f} points; {caps}")
        backend.close()
    except RootPilotError as exc:
        add("backend", FAIL, f"{cfg.backend}: {exc}")

    if cfg.backend == "zxtouch":
        from agent.backends.zxtouch import is_exposed, listening_addresses

        addrs = listening_addresses(cfg.zxtouch_port)
        if addrs is None:
            add("zxtouch exposure", WARN, "cannot check (netstat missing); see docs/SECURITY.md")
        elif is_exposed(addrs):
            add(
                "zxtouch exposure",
                FAIL,
                f"port {cfg.zxtouch_port} listens on {', '.join(addrs)}: anyone on your network "
                "can control this phone. See docs/SETUP.md, 'Lock ZXTouch to loopback'.",
            )
        else:
            add("zxtouch exposure", OK, f"loopback only ({', '.join(addrs) or 'not listening'})")

    sd = state_dir()
    try:
        ensure_private_dir(sd)
        add("state dir", OK, str(sd))
    except OSError as exc:
        add("state dir", FAIL, f"{sd}: {exc}")
    add(
        "stop file",
        WARN if stop_file().exists() else OK,
        f"{stop_file()} {'EXISTS: runs stop immediately' if stop_file().exists() else 'absent'}",
    )
    return _print_checks(checks, out)


def _print_checks(checks: list[tuple[str, str, str]], out: TextIO) -> int:
    for name, status, detail in checks:
        out.write(f"[{status:>4}] {name:<17} {detail}\n")
    failed = any(status == FAIL for _, status, _ in checks)
    out.write("\nSome checks failed.\n" if failed else "\nAll checks passed.\n")
    return 1 if failed else 0


# ---- run / repl ------------------------------------------------------------------------


def make_confirm(
    interactive: bool, yes_to: set[str], out: TextIO, read: Callable[[str], str] = input
) -> Callable[[ToolDef, dict, Decision], str]:  # type: ignore[type-arg]
    def confirm(tool: ToolDef, args: dict, decision: Decision) -> str:  # type: ignore[type-arg]
        # --yes-to only covers category defaults; never shell, sensitive apps or irreversible taps.
        if (
            decision.rule is Rule.DEFAULT
            and tool.category.value in yes_to
            and tool.category is not Category.SHELL
        ):
            return YES
        if not interactive:
            out.write(
                f"⛔ {tool.name} needs confirmation ({decision.reason}); denied (not interactive)\n"
            )
            return NO
        shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        options = (
            "[y]es / [n]o / [a]lways for this run"
            if decision.rule is Rule.DEFAULT and tool.category is not Category.SHELL
            else "[y]es / [n]o"
        )
        while True:
            try:
                answer = read(
                    f"\n⚠️  {tool.name}({shown})\n   {decision.reason}\n   Allow? {options}: "
                )
            except EOFError:
                return NO
            answer = answer.strip().lower()
            if answer in ("y", "yes"):
                return YES
            if answer in ("n", "no", ""):
                return NO
            if answer in ("a", "always") and "always" in options:
                return ALWAYS

    return confirm


def make_agent(cfg: Config, args: argparse.Namespace, out: TextIO, interactive: bool) -> Agent:
    redactor = Redactor(cfg.secrets())
    provider = make_provider(cfg, redactor)
    backend = make_backend(cfg, args.fixture)
    sd = ensure_private_dir(state_dir())
    agent = Agent(
        backend=backend,
        provider=provider,
        registry=default_registry(),
        permissions=PermissionEngine(cfg),
        config=cfg,
        confirm=make_confirm(interactive, set(args.yes_to or []), out),
        ask_user=(lambda q: input(f"\n❓ {q}\n> "))
        if interactive
        else (lambda q: "(no user available)"),
        redactor=redactor,
        report=lambda line: print(line, file=out, flush=True),
        stop_file=stop_file(),
    )
    agent.audit = AuditLog.for_run(sd, agent.run_id, redactor)
    agent.screens_dir = sd / "runs" / agent.run_id
    return agent


def _print_result(result: RunResult, out: TextIO) -> None:
    mark = "✅" if result.success else "⚠️ "
    out.write(f"\n{mark} {result.answer}\n")
    out.write(
        f"   ({result.reason}; {result.steps} steps; {result.usage.input_tokens} in / "
        f"{result.usage.output_tokens} out tokens; log: {result.log_path})\n"
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    cfg = _load(args)
    agent = make_agent(cfg, args, out, interactive=sys.stdin.isatty())
    try:
        result = agent.run(args.task)
    finally:
        agent.audit.close()
        agent.backend.close()
    _print_result(result, out)
    return 0 if result.success else 1


def repl(args: argparse.Namespace, out: TextIO) -> int:
    cfg = _load(args)
    out.write(
        f"RootPilot {__version__} — {cfg.provider} on {cfg.backend}. "
        f"Type a task, or 'quit'. Stop a run with Ctrl-C or: touch {stop_file()}\n"
    )
    while True:
        try:
            task = input("\nrootpilot> ").strip()
        except (EOFError, KeyboardInterrupt):
            out.write("\n")
            return 0
        if task in ("quit", "exit"):
            return 0
        if not task:
            continue
        agent = make_agent(cfg, args, out, interactive=True)
        try:
            _print_result(agent.run(task), out)
        finally:
            agent.audit.close()
            agent.backend.close()


# ---- entry point -------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="config file (default ~/.config/rootpilot/config.json)")
    common.add_argument("--backend", choices=["mock", "zxtouch"])
    common.add_argument("--provider", choices=list(PROVIDERS))
    common.add_argument("--model")
    common.add_argument("--fixture", help="fixture screens directory for --backend mock")

    parser = argparse.ArgumentParser(
        prog="rootpilot", parents=[common], description="On-device AI agent for jailbroken iPhones."
    )
    parser.add_argument("--version", action="version", version=f"rootpilot {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", parents=[common], help="check the setup")
    p_run = sub.add_parser("run", parents=[common], help="run one task")
    p_run.add_argument("task")
    p_run.add_argument("--max-steps", type=int)
    p_run.add_argument(
        "--save-screens", action="store_true", help="keep screenshots in the run dir"
    )
    p_run.add_argument(
        "--yes-to",
        action="append",
        metavar="CATEGORY",
        choices=[c.value for c in Category if c is not Category.SHELL],
        help="auto-confirm a category's default prompt (never shell, sensitive "
        "apps or irreversible actions)",
    )
    sub.add_parser("repl", parents=[common], help="interactive session (the default)")
    p_setup = sub.add_parser(
        "setup",
        parents=[common],
        help="choose a provider (Anthropic, DeepSeek, …) and save its key",
    )
    p_setup.add_argument(
        "--key-stdin", action="store_true", help="read the API key from stdin (for scripts)"
    )
    return parser


def main(argv: Optional[list[str]] = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    for name in ("yes_to", "max_steps", "save_screens", "key_stdin"):
        if not hasattr(args, name):
            setattr(args, name, None)
    try:
        if args.command == "doctor":
            return doctor(args, out)
        if args.command == "run":
            return run(args, out)
        if args.command == "setup":
            from agent.setup_wizard import setup

            return setup(args, out)
        return repl(args, out)
    except (RootPilotError, NotImplementedError) as exc:
        out.write(f"error: {Redactor().text(str(exc))}\n")
        return 2
    except KeyboardInterrupt:
        out.write("\ninterrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
