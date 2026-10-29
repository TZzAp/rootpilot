# Changelog

## 0.1.0 — 2026-10-29

First working version, tested on an iPhone X (iOS 16.7.11, palera1n rootful) and an iPhone 7
Plus (iOS 15.8.3, Dopamine rootless).

### Agent
- Observe → think → act loop with step, token and time budgets, post-action verification,
  untrusted-content framing, append-only history, a stop file and a JSONL audit log.
- Permission engine: allow / ask / deny tiers, sensitive apps, irreversible-tap detection,
  non-http URL schemes, password-field refusal and a shell deny-list.
- Providers: Anthropic (`claude-opus-5-5`), DeepSeek (`deepseek-flash`), OpenAI and any
  OpenAI-compatible server; `rootpilot setup` stores the key with mode 0600.

### Device
- ZXTouch backend (loopback only): touch, swipe with ease-out (no scroll glide), text input, app
  launch, OCR-based UI tree, pixel↔point conversion; home via SpringBoard.
- `scripts/zxtouch_loopback.py`: rebinds ZXTouch to 127.0.0.1, removes its passwordless-root
  sudoers entry, and with `--rootless` builds a rootless tweak-only package.

### On the phone
- Control page (`rootpilot serve`, loopback + token) behind a home-screen icon
  (`rootpilot install-icon`): start tasks, live steps, Allow/Deny prompts, Stop, run history,
  status chips. `rootpilot daemon-plist` keeps it running under launchd.
- Edge-glow overlay (`hud/`): a SpringBoard window with a rotating glow around the screen and
  touch ripples while the agent is in control. No hooks, no touches, no text.
- `rootpilot capture`: request-gated screen frames for docs and debugging.
