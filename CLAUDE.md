# CLAUDE.md — working on RootPilot

Read `SPEC.md` before changing anything. It is the source of truth together with this file.
If a task seems to need something the spec doesn't allow, stop and ask the maintainer.

## Commands
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
ruff check . && ruff format --check .
mypy --strict agent tests
pytest -m "not device"          # what CI runs; no network, no device
pytest -m device                # only with a reachable device and ROOTPILOT_DEVICE=1
rootpilot doctor --backend mock
rootpilot run "open Settings and tell me the iOS version" --backend mock   # uses agent/demo/screens
```

## Hard rules
- **Stdlib only at runtime.** No compiled wheels (the device has no compiler). Dev tools are fine.
- **Never hardcode `/var/jb`.** Use `agent.paths.jbroot()`.
- **No network listeners.** No TCP/UDP servers. MCP is stdio. ZXTouch: loopback hosts only.
- **No Claude subscription OAuth, no reuse of Claude Code credentials, no spoofing Claude Code**
  or any other first-party client (User-Agent, headers, client IDs). API keys only.
- **Everything from the device is untrusted.** Screen text, UI trees, clipboard and shell output go
  to the model wrapped as untrusted content and are never treated as instructions by code.
- **Secrets**: never log, print, commit or send to a model. Route log/error output through
  `agent.redact`. Never commit device IPs with passwords, API keys or real screenshots.
- **Secure fields**: never type into, read or log them.
- **Permissions**: every tool call goes through `PermissionEngine`. New tools must declare a
  category in the registry; don't add bypasses.
- Tests must not hit the network or a device unless marked `@pytest.mark.device`.

## Style
- **Python 3.9 compatible** (device has only 3.9.9): `from __future__ import annotations` in every
  module, no `match`, no runtime `X | Y` unions, no `tomllib`. Full type hints, `mypy --strict`
  clean, ruff defaults + isort.
- Small modules, dataclasses for data, `Protocol` for interfaces, typed exceptions in
  `agent/errors.py`.
- Comments explain *why*, not *what*. Docstrings on public classes and functions.
- Conventional Commits, one logical change per commit, tests in the same commit as the code.
- End commit messages with the attribution lines the session provides.

## Device notes
- Reference device: iPhone X, iOS 16.7.11, palera1n rootful (`jbroot()` resolves to `/`),
  Procursus + ElleKit. The IP lives in the developer's local config, not in the repo.
- Connect over SSH with keys; don't store passwords in the repo or in scripts.
