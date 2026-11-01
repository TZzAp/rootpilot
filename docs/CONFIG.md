# Configuration

The easiest way to create the file is `rootpilot setup`, which asks which provider to use,
reads the API key without echoing it, and writes the file with mode `0600`. For scripts:
`echo "$KEY" | rootpilot setup --provider deepseek --key-stdin`.

Settings come from, in increasing priority: built-in defaults → `~/.config/rootpilot/config.json`
(or `$XDG_CONFIG_HOME/rootpilot/config.json`, or `--config PATH`) → environment variables → CLI
flags. The file is JSON; unknown keys are an error. A file containing API keys must be mode
`0600`.

| Key | Env var | Default | Meaning |
|---|---|---|---|
| `provider` | `ROOTPILOT_PROVIDER` | `anthropic` | `anthropic`, `deepseek`, `openai`, `compat` (`chatgpt` arrives in M4) |
| `model` | `ROOTPILOT_MODEL` | `claude-opus-5-5` (anthropic), `deepseek-flash` (deepseek) | Required for `openai` and `compat` |
| `backend` | `ROOTPILOT_BACKEND` | `zxtouch` | `zxtouch` or `mock` |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | | |
| `openai_api_key` | `OPENAI_API_KEY` | | |
| `deepseek_api_key` | `DEEPSEEK_API_KEY` | | from platform.deepseek.com |
| `compat_base_url` | `ROOTPILOT_COMPAT_BASE_URL` | | e.g. `http://127.0.0.1:8080/v1` |
| `compat_api_key` | `ROOTPILOT_COMPAT_API_KEY` | | optional |
| `compat_vision` | | `true` | set `false` for text-only local models |
| `zxtouch_host` | `ROOTPILOT_ZXTOUCH_HOST` | `127.0.0.1` | loopback only |
| `zxtouch_port` | `ROOTPILOT_ZXTOUCH_PORT` | `6000` | |
| `serve_port` | `ROOTPILOT_SERVE_PORT` | `8366` | control page port (loopback only) |
| `effort` | `ROOTPILOT_EFFORT` | (provider default) | thinking: `off`, `low`, `medium`, `high`, `max` |
| `ocr` | | `auto` | screen reading: `auto` (the overlay if installed), `hud`, `zxtouch` (leaks SpringBoard memory) |
| `max_steps` | | `40` | model calls per run (`--max-steps`) |
| `max_tokens` | | `400000` | input + output tokens per run |
| `max_seconds` | | `600` | wall-clock limit per run |
| `max_output_tokens` | | `16000` | per model call; thinking counts toward it |
| `image_max_edge` | | `1280` | screenshots are downscaled to this long edge (px) |
| `screenshots` | | `true` | send screenshots to models that read images; `false` = UI tree (OCR text) only, fewer tokens |
| `keep_screenshots` | | `3` | OpenAI-style providers: newest screenshots kept in context |
| `save_screens` | | `false` | keep screenshots in the run directory (`--save-screens`) |
| `sensitive_apps` | | `[]` | extra bundle IDs or globs, e.g. `["com.*bank*"]` |
| `irreversible_words` | | `[]` | extra labels that need confirmation when tapped |
| `shell_allow_reboot` | | `false` | let `reboot`/`ldrestart` reach the confirmation prompt |
| `category_defaults` | | `{}` | e.g. `{"clipboard_read": "allow", "act": "ask"}`; values `allow`/`ask`/`deny` |

Models that read images (Anthropic, OpenAI, DeepSeek `deepseek-flash` since V4.1) get a
screenshot with each observation, next to the UI tree; the newest `keep_screenshots` stay in the
context. On ZXTouch the screenshot comes from the overlay (it needs `hud/`). Text-only models
(`deepseek-v4-pro`, or `compat` with `compat_vision: false`) and `screenshots: false` get the UI
tree only. A 375×812 screenshot costs DeepSeek about 230 input tokens.

Categories: `observe`, `act`, `app`, `clipboard_read`, `shell`, `meta`. Escalations (sensitive
apps, irreversible taps, non-http URLs, password fields, the shell deny-list) apply on top of
`category_defaults` and can't be turned off; see [`SECURITY.md`](SECURITY.md).

Example:

```json
{
  "provider": "anthropic",
  "anthropic_api_key": "sk-ant-...",
  "backend": "zxtouch",
  "max_steps": 30,
  "sensitive_apps": ["com.*bank*", "com.paypal.*"],
  "category_defaults": {"act": "allow"}
}
```
