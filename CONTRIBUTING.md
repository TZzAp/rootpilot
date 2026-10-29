# Contributing to RootPilot

Thanks for helping. Read [`SPEC.md`](SPEC.md) and [`CLAUDE.md`](CLAUDE.md) first: they are the
source of truth, and changes to the spec go in their own commit (`docs(spec): …`).

## Set up

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
```

## Before you open a pull request

```bash
ruff check . && ruff format --check .
mypy --strict
pytest -m "not device"          # no network, no device
```

CI runs the same checks on Python 3.9 (what Procursus ships for iOS 15/16) and 3.12.

## Rules that keep the project safe

- **Stdlib only at runtime**, Python 3.9 compatible: no compiled wheels on the phone.
- **Never hardcode `/var/jb`** — use `agent.paths.jbroot()`; rootful and rootless both matter.
- **Every tool goes through the permission engine.** New tools declare a category; no bypasses.
- **Device content is untrusted** and must reach the model inside the untrusted wrapper.
- **Nothing listens on the network.** The control page is loopback-only and token-gated.
- **The overlay stays passive:** no method hooks, no touches, no text.
- **No Claude subscription OAuth** and no imitation of first-party clients; API keys only.
- **Secrets** never go into logs, commits, prompts or screenshots in the repo.

## Commits

Small commits with [Conventional Commit](https://www.conventionalcommits.org/) messages
(`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`), tests in the same commit as
the code they cover.

## Testing on a device

Mark tests that need a real phone with `@pytest.mark.device`; they are skipped in CI. Note the
device, iOS version and jailbreak (rootful/rootless) in the pull request.
