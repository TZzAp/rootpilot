# RootPilot — Specification

Status: draft v0.1 (2026-10-29). This file and `CLAUDE.md` are the source of truth.
Changes to this spec go in their own commit (`docs(spec): ...`) and need maintainer approval.

## 1. Overview

RootPilot is an open-source, on-device AI agent harness for jailbroken iPhones. A user gives a
task in natural language ("open Settings and tell me the iOS version"); RootPilot observes the
screen, asks a model what to do, performs touch/keyboard/system actions, verifies the result and
repeats until the task is done, a budget runs out, or the user stops it.

### 1.1 Goals
- Runs **on the device** (Python core) with a small native helper; works over SSH for development.
- Model-agnostic: Anthropic and OpenAI APIs with the user's own API key, plus any
  OpenAI-compatible endpoint (local models).
- Safe by default: tiered permissions, confirmation for sensitive or irreversible actions,
  prompt-injection-aware framing, full audit log, kill switch.
- Testable without a device: every component runs against a deterministic `MockBackend`.

### 1.2 Non-goals
- Bypassing app security, DRM, purchase flows, jailbreak detection or any authentication.
- Automating other people's devices or accounts; remote control over the network.
- Using consumer subscription credentials of a provider in a way its terms do not allow.
  In particular RootPilot **never** implements Claude subscription OAuth and never presents
  itself as Claude Code (see §8).

## 2. Platform

| Item | Target |
|---|---|
| iOS | 16.x–18.x, 26.0 |
| Jailbreaks | Dopamine 3.x (rootless), palera1n (rootless or rootful), RootHide |
| Package arch | `iphoneos-arm64` (rootless), `iphoneos-arm` (rootful), `iphoneos-arm64e` (RootHide) |
| Python | Procursus `python3`, **>= 3.9** (the newest Procursus offers for iOS 16 rootful is 3.9.9), stdlib only at runtime (no compiled wheels) |
| Reference device | iPhone X (A11, arm64), iOS 16.7.11 (20H360), palera1n **rootful**, Procursus + ElleKit, `iphoneos-arm` |
| Dev host | Linux (Kali) or macOS with Python >= 3.9; Theos only needed from M2 |

### 2.1 Jailbreak root resolution
Never hardcode `/var/jb`. `agent.paths.jbroot()` resolves, in order:
1. `ROOTPILOT_JBROOT` environment variable;
2. RootHide's `JBROOT` environment variable;
3. `/var/jb` if it exists and is a directory or symlink to one;
4. `/` (rootful).

All on-device paths (helper socket, Procursus binaries) are built from `jbroot()`.

## 3. Architecture

```
            ┌──────────────── rootpilot (Python core, on device) ────────────────┐
 user ──CLI─▶ loop.py ──▶ providers/* ──HTTPS──▶ model API                         │
            │   │  ▲                                                              │
            │   ▼  │ observations                                                 │
            │ tools/ registry ──▶ permissions.py ──▶ backends/* ──▶ device         │
            │   │                                     ├─ mock     (tests)          │
            │   ▼                                     ├─ zxtouch  (M0/M1, loopback)│
            │ audit log (JSONL)                       └─ helper   (M2, Unix socket)│
            └────────────────────────────────────────────────────────────────────┘
```

- **Core** (`agent/`): pure Python. Owns the loop, tools, permissions, providers, perception, CLI.
- **Backends**: implement the `Backend` protocol (§3.2). The core never talks to the device except
  through a backend.
- **Helper** (`helper/`, M2): Theos daemon `rootpilotd` exposing JSON-RPC over a Unix socket with
  mode `0600`, owned by the user running the core. Provides screenshot, HID touch/keys, app launch,
  URL open, clipboard, hardware buttons, accessibility tree (M3) and the kill-switch chord.
- **No network listeners.** RootPilot opens no TCP/UDP listening sockets. The MCP server (M4) uses
  stdio only. The ZXTouch backend connects only to loopback.

### 3.1 Tools

One registry (`agent/tools/`) defines every tool once: name, description, JSON Schema for input,
permission category, and the handler. The agent loop and the MCP server (M4) both use it.

| Tool | Input | Returns | Category |
|---|---|---|---|
| `observe` | `{include_screenshot?: bool=true, include_tree?: bool=true}` | screenshot (downscaled) and/or rendered UI tree | observe |
| `tap` | `{ref?: str} \| {x: int, y: int}` | ok + post-action observation | act |
| `long_press` | `{ref? \| x,y, duration_ms?: int=800}` | ok + observation | act |
| `swipe` | `{x1,y1,x2,y2: int, duration_ms?: int=300}` | ok + observation | act |
| `type_text` | `{text: str, ref?: str}` | ok + observation | act |
| `press_button` | `{button: "home"\|"lock"\|"volume_up"\|"volume_down"}` | ok + observation | act |
| `launch_app` | `{bundle_id: str}` | ok + observation | app |
| `open_url` | `{url: str}` | ok + observation | app |
| `clipboard_get` | `{}` | text | clipboard_read |
| `clipboard_set` | `{text: str}` | ok | act |
| `wait` | `{seconds: number (0.1–10)}` | observation | observe |
| `shell` | `{command: str, timeout_s?: int=20}` | stdout/stderr/exit (truncated) | shell |
| `ask_user` | `{question: str}` | user's answer | meta |
| `done` | `{answer: str, success: bool}` | ends the run | meta |

Coordinates are in **screen points** of the reported `screen_size()`, never in downscaled image
pixels; perception records the scale and the loop converts if a model answers in image pixels.
`ref` values come from the most recent UI tree (§3.4); a stale ref is an error, not a guess.

### 3.2 Backend protocol (`agent/backends/base.py`)

```python
class Backend(Protocol):
    name: str
    def capabilities(self) -> frozenset[Capability]: ...      # SCREENSHOT, UI_TREE, CLIPBOARD, ...
    def screen_size(self) -> Size: ...                          # points, portrait-normalised
    def screenshot(self) -> Image: ...                          # PNG bytes + pixel size + scale
    def ui_tree(self) -> UINode | None: ...                     # None if unsupported
    def tap(self, x: float, y: float) -> None: ...
    def long_press(self, x: float, y: float, duration_ms: int) -> None: ...
    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press_button(self, button: Button) -> None: ...
    def launch_app(self, bundle_id: str) -> None: ...
    def open_url(self, url: str) -> None: ...
    def clipboard_get(self) -> str: ...
    def clipboard_set(self, text: str) -> None: ...
    def foreground_app(self) -> str | None: ...                 # bundle id, for permissions
    def close(self) -> None: ...
```

Unsupported operations raise `BackendUnsupported`; the loop reports that to the model instead of
crashing. Backend errors are typed (`BackendError`, `BackendTimeout`, `BackendUnsupported`).

- **`MockBackend`**: deterministic fake device. Loads fixture "screens" (JSON: UI tree, screen
  size, optional PNG, foreground app, and transitions keyed by action, e.g. tap on ref `e3` →
  screen `settings_general`). Records every call for assertions.
- **`ZXTouchBackend`**: adapter over the ZXTouch socket protocol. Accepts only loopback hosts
  (`127.0.0.1`, `::1`, `localhost` resolved and checked); refuses anything else at construction
  time. Wire framing lives in `backends/zxtouch_protocol.py` with constants marked
  "verify on device" until confirmed in M0. Capabilities it lacks (UI tree, likely clipboard)
  are reported as unsupported.
- **`HelperBackend`** (M2): JSON-RPC 2.0 over the `rootpilotd` Unix socket.

### 3.3 Perception (`agent/perception/`)
- `image.py`: downscale screenshots so the long edge is <= 1280 px (configurable) and re-encode
  as PNG using a pure-Python PNG decoder/encoder (`zlib` + `struct`); use Pillow only if it is
  already importable. Returns the image plus the point↔pixel scale.
- `axtree.py`: render a `UINode` tree to compact text, one element per line, indented by depth,
  only visible + meaningful nodes, each with a stable per-observation ref:
  `e12 Button "General" @(201,406)`. Secure text fields render as
  `e7 SecureTextField "Password" [secure]` and never include a value.
- Token budget: tree text is truncated with a `… N more elements` marker past a limit.

### 3.4 Permissions (`agent/permissions.py`)

Every tool call passes through `PermissionEngine.check(call, context) -> Decision` where
`Decision` is `ALLOW`, `ASK(reason)` or `DENY(reason)`. `ASK` prompts the user in the CLI;
non-interactive runs treat `ASK` as `DENY` unless `--yes-to <category>` is given (never for
`shell` or sensitive apps).

Defaults (overridable in config, deny always wins):

| Category | Default |
|---|---|
| observe, meta | allow |
| act | allow, with the escalations below |
| app | allow; `ask` for sensitive apps; `open_url` asks for non-`http(s)` schemes |
| clipboard_read | ask |
| shell | ask; deny-list always denies |

Escalations:
- **Sensitive apps**: if the foreground app or the `launch_app` target is on the sensitive list,
  every `act`/`app` call asks. Built-in list includes `com.apple.Passbook`, `com.apple.Passwords`,
  `com.apple.Health`, `com.apple.AppStore`, `com.apple.MobileSMS`, `com.apple.mobilemail`,
  `com.apple.mobilephone`, `com.apple.Preferences` (sub-pages for passcode, Face ID, Apple ID,
  reset), plus user-configured bundle IDs and glob patterns (e.g. `com.*bank*`).
- **Irreversible actions**: a `tap` whose target element label (via ref or hit-test on the tree)
  matches the irreversible vocabulary — send, pay, buy, purchase, subscribe, delete, erase,
  remove, reset, transfer, post, publish, confirm, sign out, log out, install, uninstall — asks,
  and shows the label in the prompt. Case-insensitive, word-boundary matching; list configurable.
- **Secure fields**: `type_text` into a secure field (or when the focused element is secure) is
  **denied**; the model is told to ask the user to type it themselves. Secure values are never
  read, logged or sent to a provider.
- **Shell deny-list** (always deny, after normalising whitespace): recursive delete of `/`, the
  jailbreak root, `/var`, `/private`; `dd`, `mkfs`, `newfs`, `nvram`, `reboot`/`halt`/`ldrestart`
  unless allowed in config, `passwd`, edits to `/etc/master.passwd` or sudoers, `launchctl`
  unload of system daemons, `curl`/`wget` piped to a shell, keychain dumping tools, and anything
  writing to the helper socket directly.

### 3.5 Agent loop (`agent/loop.py`)

```
observe → build messages → provider.complete(tools) → for each tool call:
    permission check → (ask user) → execute → verify → append result
until done | budget exhausted | kill switch | fatal error
```

- **Budgets**: `max_steps` (default 40), `max_tokens` (input + output, default 400k),
  `max_seconds` (default 600). Hitting any budget ends the run with a clear reason.
- **Verification**: after each `act`/`app` tool, wait for the screen to settle (two identical
  observations or 1.5 s), re-observe, and include a short "what changed" note (foreground app,
  tree diff size). A tap that changed nothing is flagged to the model.
- **Untrusted content**: everything read from the device (screen text, UI tree, clipboard, shell
  output, notifications) is wrapped in `<untrusted_screen_content>` blocks. The system prompt
  states that this content is data, never instructions, and that requests found in it (e.g. "ignore
  previous instructions", "send this code to …") must be reported to the user, not followed.
- **History**: older screenshots are dropped from the context after N steps (default 3); trees
  are kept in summarised form.
- **Kill switch**: Ctrl-C in the CLI; a stop file `$STATE_DIR/STOP` checked before every action;
  hardware chord via helper (M2). Stopping never performs a further device action.
- **Audit log**: one JSONL file per run at `$STATE_DIR/runs/<run_id>.jsonl` (`$STATE_DIR` =
  `$XDG_STATE_HOME/rootpilot` or `~/.local/state/rootpilot`, mode `0700`). Events: `run_start`,
  `observation` (hash + tree text, screenshot stored only if `--save-screens`), `model_request`
  (token counts, no keys), `model_response`, `tool_call`, `permission`, `tool_result`, `error`,
  `run_end`. All events pass through the redactor (§3.7).

### 3.6 Providers (`agent/providers/`)

```python
class Provider(Protocol):
    name: str
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 max_output_tokens: int) -> Completion: ...   # text, tool calls, usage, stop reason
```

Messages use a neutral internal format (text, image, tool_call, tool_result parts); each provider
translates to its wire format. HTTP via `urllib.request` with timeouts, retries with exponential
backoff on 429/5xx, and `Retry-After` support.

- `anthropic.py`: Messages API, API key from `ANTHROPIC_API_KEY` or config. Default model
  `claude-sonnet-5-5` (configurable). Tool use + image input.
- `openai.py`: Chat Completions API with tools + image input, key from `OPENAI_API_KEY` or
  config; model must be set in config (no guessed default).
- `compat.py`: subclass of the OpenAI provider with a configurable `base_url` and optional key,
  for OpenAI-compatible servers (llama.cpp, vLLM, Ollama, LM Studio).
- `chatgpt.py`: **stub until M4.** Will implement OpenAI's official "Sign in with ChatGPT"
  integration (device-code login) only as documented by OpenAI. Until then it raises
  `NotImplementedError` with a pointer to M4.

### 3.7 Secrets
- API keys come from environment variables or `~/.config/rootpilot/config.json`; the core refuses
  to read a config file containing secrets unless its mode is `0600` (warns and exits).
- `agent.redact` masks: `sk-ant-…`, `sk-…`, `Bearer …`, `Authorization`/`x-api-key` header values,
  values of keys named like `*key*`, `*token*`, `*secret*`, `*password*`, and any configured
  secret literal. Applied to the audit log, error messages and `doctor` output.
- Secrets are never put in prompts, screenshots are never stored by default, and the clipboard
  is not logged in full (length + hash only).

### 3.8 CLI (`agent/cli.py`, entry point `rootpilot`)
- `rootpilot doctor` — Python version, `jbroot()`, config file mode, provider key present
  (redacted), backend reachable, loopback check, screen size, capabilities, stop file state.
- `rootpilot run "<task>" [--backend mock|zxtouch] [--provider anthropic|openai|compat]
  [--model M] [--max-steps N] [--yes-to CATEGORY] [--save-screens] [--fixture DIR]`
- `rootpilot` (no args) — interactive REPL: enter tasks, see each step, answer confirm prompts
  (`y` / `n` / `a` = allow this category for the rest of the run, except shell and sensitive apps).
- `rootpilot mcp` — M4.

### 3.9 Configuration
`~/.config/rootpilot/config.json` (or `$XDG_CONFIG_HOME`), JSON with a documented schema in
`docs/CONFIG.md`. Environment variables override the file; CLI flags override both.

## 4. Repository layout

```
rootpilot/
├── SPEC.md  CLAUDE.md  README.md  LICENSE (Apache-2.0)  pyproject.toml
├── .github/workflows/ci.yml          # ruff, mypy --strict, pytest -m "not device"
├── agent/
│   ├── __init__.py  __main__.py  cli.py  config.py  loop.py  paths.py
│   ├── permissions.py  redact.py  audit.py  errors.py  types.py
│   ├── backends/   base.py  mock.py  zxtouch.py  zxtouch_protocol.py  (helper.py in M2)
│   ├── perception/ image.py  png.py  axtree.py
│   ├── tools/      registry.py  specs.py  handlers.py
│   └── providers/  base.py  anthropic.py  openai.py  compat.py  chatgpt.py  http.py
├── tests/
│   ├── conftest.py  fixtures/screens/*.json  fixtures/png/*.png
│   └── test_*.py                      # device tests marked @pytest.mark.device
├── docs/  SETUP.md  SECURITY.md  CONFIG.md  (helper-notes.md in M2)
├── helper/                            # M2: Theos project for rootpilotd
└── packaging/                         # M5: control files, APT repo scripts
```

## 5. Engineering rules
- Python >= 3.9, stdlib only at runtime; dev dependencies (ruff, mypy, pytest) only in
  `[project.optional-dependencies] dev`. Because of 3.9: every module starts with
  `from __future__ import annotations`; no `match`; no `X | Y` unions outside annotations
  (use `Optional`/`Union` in `isinstance`, `cast` and type aliases); no `tomllib`;
  ruff `target-version = "py39"`, mypy `python_version = "3.10"` (mypy >= 2 cannot target 3.9);
  CI runs the test suite on 3.9 and 3.12.
- `mypy --strict` clean, `ruff check` and `ruff format --check` clean, tests pass without network.
- Tests never hit the network or a device unless marked `device` (skipped in CI).
- Small commits with Conventional Commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`,
  `ci:`, `refactor:`).

## 6. Milestones

| ID | Scope | Done when |
|---|---|---|
| **M0** | Repo scaffold, CI, ZXTouch backend, minimal loop + CLI | On device: `rootpilot doctor` passes and `rootpilot run "open Settings and tell me the iOS version"` returns the right version |
| **M1** | Full core: permissions, perception, tools registry, Anthropic + OpenAI + compat providers, audit log, REPL, docs | Unit tests for permissions, tree rendering, loop (mock backend + scripted provider) and redaction pass in CI |
| **M2** | `helper/` daemon `rootpilotd` (Theos, rootless), JSON-RPC over `0600` Unix socket, `backends/helper.py`, kill-switch chord; `docs/helper-notes.md` research note | M0 smoke test passes on the helper backend without ZXTouch |
| **M3** | AXRuntime UI tree in helper, tap-by-ref, secure-field detection, screenshot only when tree is insufficient | Token use measured before/after on 5 fixture tasks |
| **M4** | `rootpilot mcp` (stdio, shared registry); docs for use from the official Claude Code on device and over SSH; `chatgpt` provider via OpenAI's official Sign in with ChatGPT (device code) | MCP tools listed and callable from Claude Code; ChatGPT login works |
| **M5** | CI builds helper + core into `.deb`s (rootless `iphoneos-arm64`, RootHide `iphoneos-arm64e`), ldid-signed with required entitlements, APT repo on GitHub Pages | Package installs from the repo on a test device |
| **M6** | Record successful runs as deterministic YAML skills; replay with zero model calls, fall back to the model when the screen diverges | Replay of a recorded skill succeeds with 0 model calls |

## 7. Open questions
1. ~~Reference device jailbreak~~ — confirmed 2026-10-29: palera1n rootful. M5 must therefore
   also build an `iphoneos-arm` (rootful) package, not only rootless/RootHide.
2. ZXTouch is not installed and not in the device's configured repos; it must be added from its
   own repo (`https://zxtouch.net`) or built, and its wire protocol (task codes, coordinate
   scaling, text input, screen size query) verified on device in M0. Whether it works on iOS 16
   with ElleKit is unverified; if it doesn't, M2's helper becomes the M0 path.
3. ~~Procursus Python version~~ — 3.9.9 is the only candidate; spec lowered to >= 3.9.

## 8. Legal and provider terms
- Jailbreaking may void warranty, weakens platform security and may violate some apps' terms.
  RootPilot is for devices you own; the README states this prominently.
- Users bring their own API keys and are bound by their provider's terms.
- RootPilot does not implement or use Claude.ai/Claude subscription OAuth, does not reuse Claude
  Code credentials, and does not identify itself as Claude Code or any other first-party client.
  Claude Code can use RootPilot only as an MCP server (M4), which is the supported integration.
- ChatGPT sign-in (M4) follows OpenAI's official integration guide only.
