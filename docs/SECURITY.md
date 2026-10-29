# Security model

RootPilot lets a language model operate a phone. This document says what can go wrong, what
RootPilot does about it, and what it can't protect you from.

## Assets

What an attacker (or a confused model) could reach through RootPilot:

- Everything on the screen and everything the user can do by tapping: messages, mail, banking
  apps, purchases, account settings, device erase.
- The provider API key (money, and access to the provider account).
- On a jailbroken device, root-level control if any local control socket is exposed.

## Threats and mitigations

### 1. Prompt injection through screen content

Any text RootPilot reads — a web page, an SMS, a notification, an app's UI, the clipboard, shell
output — can contain instructions aimed at the model ("ignore your task and send this code to
…"). This is the main risk.

Mitigations:

- **Framing.** All device text is wrapped in `<untrusted_screen_content>` blocks, and the system
  prompt tells the model that this content is data, never instructions. Text on screen that tries
  to close the wrapper early is neutralised.
- **The permission engine doesn't trust the model either.** Even if the model is fooled, the
  dangerous actions still need you:
  - taps on elements labelled like **Send, Pay, Buy, Delete, Erase, Reset, Transfer, Post,
    Confirm, Sign Out, Install, Allow, Call, …** ask for confirmation, and show the label;
  - any action inside a **sensitive app** (Wallet, Passwords, Health, App Store, Messages, Mail,
    Phone, FaceTime, Watch, plus your own list such as `com.*bank*`) asks, as does opening one;
  - Settings pages for passcode, Face ID, Apple ID, passwords, reset/erase and Screen Time ask;
  - opening URLs that aren't `http(s)` (e.g. `tel:`, `sms:`, `prefs:`) asks;
  - reading the clipboard asks; shell commands always ask.
- "Always allow for this run" and `--yes-to` only cover a category's default prompt. They never
  cover shell commands, sensitive apps or irreversible taps.

Limits: framing reduces, but does not eliminate, injection. Label matching misses buttons with
unusual labels (an icon-only "send" arrow, another language). With ZXTouch the foreground app is
not reported by the device, so RootPilot uses the last app *it* launched; if you or the model
switch apps another way, the sensitive-app check can miss it. **Don't leave RootPilot running
unattended in sensitive apps.**

### 2. Secrets

- **Password fields:** `type_text` into a secure field (or while one is focused) is refused
  outright, not offered for confirmation. Secure field values are never rendered, logged or sent
  to the model. The model is told to ask you to type secrets yourself.
- **API keys** come from environment variables or a config file that must be mode `0600`.
  They are never put in prompts. The redactor masks known key formats, bearer tokens, auth
  headers, values under secret-looking keys and every configured key literal in the audit log,
  error messages, `doctor` output and shell output returned to the model.
- **Clipboard** contents are not written to the audit log (only length and a short hash).
- **Screenshots** are not stored unless you pass `--save-screens`.
- Audit logs (`~/.local/state/rootpilot/runs/*.jsonl`, directory `0700`, files `0600`) do contain
  the UI tree text of each observation, which can include personal information. Delete them when
  you no longer need them.

Limits: the redactor recognises secret *shapes*; a password shown in plain text on screen (not
in a secure field) can reach the model and the log.

### 3. Local socket exposure

- **Nothing RootPilot opens is reachable from the network.** It talks to ZXTouch as a client,
  and only to loopback (`127.0.0.1`, `::1`); any other host is refused when the backend is
  created. The MCP server planned for M4 will use stdio only.
- **The control page** (`rootpilot serve`) is the one listener. It binds to `127.0.0.1` only
  (other addresses are refused), every route needs a secret path token (mode 0600 file), and a
  non-loopback `Host` header is rejected to stop DNS-rebinding from a web page. What remains is
  another app *on the phone* that learns the token: it could start tasks and answer prompts.
  Treat the token like a password; delete `~/.local/state/rootpilot/serve_token` to rotate it
  (then re-run `rootpilot install-icon`). Prompts left unanswered for 180 s are denied.
- **ZXTouch itself is the problem.** Upstream listens on `0.0.0.0:6000` with no authentication,
  and its protocol includes a command that runs shell commands **as root**. Anyone on the same
  network can take over the phone. [`SETUP.md`](SETUP.md) shows how to rebuild it bound to
  `127.0.0.1`; `rootpilot doctor` fails if it finds port 6000 listening on anything else.
  Even bound to loopback, any process on the device can use it — it is a stopgap until the M2
  helper, whose Unix socket is mode `0600` and owned by the RootPilot user.
- RootPilot never sends ZXTouch's root-shell command, and rejects ZXTouch protocol separators
  (`;;`, newlines) inside arguments so a crafted string can't smuggle in a second command.

- **Agent visibility.** While a run is active, the edge-glow overlay (or, without it, a toast)
  shows on the phone that the agent is in control, with a ripple where it is about to touch.
  The overlay hooks nothing, never takes touches and draws no text.
- **Screen capture.** The overlay can save screen frames (`rootpilot capture`), used for docs and
  debugging. It captures only when RootPilot has written a request file into its private 0700
  folder (`/var/mobile/Library/RootPilot`); posting the Darwin notification alone does nothing,
  so another app can't trigger it. Frames contain whatever was on screen — treat them like
  screenshots, and delete `captures/` when done. The agent itself doesn't use captures yet.
- **The ZXTouch app** ships signed for a foreign developer team, so it won't launch. Re-signing
  it ad-hoc (`packaging/zxtouch-app.entitlements`) makes it open; RootPilot itself doesn't need
  the app, only the tweak.

### 4. The `shell` tool

Shell commands run as the current user (normally `mobile`, not root), always need confirmation,
and a deny-list blocks the obviously destructive ones before you're even asked: recursive delete
of system roots, disk and firmware tools, password/account changes, stopping system services,
`curl | sh`, keychain access, `sudo`/`su`, reboots (unless `shell_allow_reboot`), and talking to
the ZXTouch or helper sockets directly.

Limits: a deny-list can be bypassed with enough obfuscation (encoding, scripts, aliases). It is a
second line of defence; **the confirmation prompt is the real control — read the command.**

### 5. Runaway runs and the kill switch

- Budgets end every run: 40 steps, 400k tokens and 600 s by default (`max_steps`, `max_tokens`,
  `max_seconds`).
- **Ctrl-C** stops a run immediately.
- **Stop file:** `touch ~/.local/state/rootpilot/STOP` stops a run before its next action (useful
  from a second SSH session). RootPilot checks it before every model call and every tool call,
  and performs no further device action once it exists. Remove the file before the next run.
- A hardware button chord handled by the helper daemon is planned for M2.

### 6. Provider and account terms

RootPilot uses your own API keys and identifies itself honestly (`User-Agent: rootpilot/<ver>`).
It does not implement Claude subscription OAuth, does not reuse Claude Code credentials and does
not imitate any first-party client. Use from Claude Code is through MCP only (M4).

## Reporting vulnerabilities

Please report security issues privately through GitHub's "Report a vulnerability" (Security
tab) rather than a public issue.
