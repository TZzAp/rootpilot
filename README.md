# RootPilot

Open-source, on-device AI agent harness for jailbroken iPhones.

Give it a task in plain language — *"open Settings and tell me the iOS version"* — and RootPilot
looks at the screen, asks a model what to do, taps and types on the device, checks the result and
repeats until the task is done. It runs on the phone itself, with your own API key, and asks
before doing anything sensitive or irreversible.

> **Status: early development (M0/M1).** Expect breaking changes. See [`SPEC.md`](SPEC.md) for
> the design and roadmap.

## Features
- Agent loop with step, token and time budgets and post-action verification
- Providers: Anthropic, DeepSeek and OpenAI (your API key), any OpenAI-compatible endpoint
  (local models); `rootpilot setup` picks one and stores the key
- Backends: a deterministic mock device for tests and ZXTouch (loopback only); a native helper
  daemon is planned (M2)
- Permission engine: allow / ask / deny tiers, sensitive-app list, confirmation for irreversible
  taps ("Send", "Pay", "Delete"…), shell deny-list, refusal to type into password fields
- JSONL audit log of every run, with secrets redacted
- On the phone: a **RootPilot home-screen icon** that opens a control page (start tasks, watch
  each step, Allow/Deny prompts, Stop, run history), and an optional **edge glow** around the
  screen while the agent is in control, with a ripple where it touches
- Pure-Python, stdlib-only runtime (Python >= 3.9) — nothing to compile on the device

## Quick start (no device needed)
```bash
git clone https://github.com/TZzAp/rootpilot && cd rootpilot
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
rootpilot doctor --backend mock
pytest -m "not device"
```
On a device, follow [`docs/SETUP.md`](docs/SETUP.md).

## Security & legal — read this first
- **Jailbreaking is risky.** It weakens iOS security, may void your warranty and may break the
  terms of some apps. Only use RootPilot on a device you own.
- **An AI agent can tap anything you can.** RootPilot asks before sensitive or irreversible
  actions, but no permission system is perfect. Don't leave it running unattended in banking,
  payment, messaging or account-settings apps. Stop it any time with Ctrl-C or the stop file.
- **Screen content is untrusted.** A web page, message or notification can contain text that
  tries to steer the agent (prompt injection). RootPilot marks screen content as data, not
  instructions, but you should still review what it does. See [`docs/SECURITY.md`](docs/SECURITY.md).
- **Your API keys, your terms.** You bring your own provider API key and are bound by that
  provider's terms of service and usage policies. Keys are read from environment variables or a
  `0600` config file and are redacted from logs.
- **No Claude subscription OAuth.** RootPilot does not log in with a Claude.ai / Claude Pro / Max
  subscription, does not reuse Claude Code credentials and never identifies itself as Claude Code.
  Use an Anthropic API key, or use RootPilot from Claude Code as an MCP server (planned, M4).
- **Nothing listens on the network.** The optional control page binds to `127.0.0.1` only and
  needs a secret token. Keep ZXTouch bound to `127.0.0.1` too (see setup docs) — otherwise anyone
  on your Wi-Fi could control your phone.

## License
[Apache-2.0](LICENSE)
