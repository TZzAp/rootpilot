# RootPilotHUD — edge glow while the agent is in control

A SpringBoard overlay that shows, on the phone itself, that RootPilot is driving the screen:
a rotating clay/amber glow around the screen edge, and a ripple where the agent is about to
touch. It appears when a run starts and fades out when it ends.

It is deliberately passive:

- **No hooks.** It patches no iOS methods. It only listens for the Darwin notifications the
  RootPilot loop posts (`com.rootpilot.hud.active` / `.update` / `.idle`) and reads
  `/var/mobile/Library/RootPilot/hud.json` for the touch point (a fixed path: on rootless
  jailbreaks the shell home and SpringBoard's home differ).
- **Never takes touches.** Its window returns `nil` from `hitTest:`, so taps — yours and the
  agent's — go straight to the app underneath.
- **Draws no text.** RootPilot reads the screen with OCR; text in the overlay would end up in
  its observations. The current step is on the control page instead. With the overlay
  installed, RootPilot also stops showing its start/stop toast for the same reason.
- **Reports its state** to `/var/mobile/Library/RootPilot/hud.status.json` (loaded, visible, PID),
  which `rootpilot doctor` and the control page read.

## Services it provides

SpringBoard can do a few things an SSH-started Python process can't, so the overlay offers them
through request/response files in the private folder `/var/mobile/Library/RootPilot` (`0700`):
RootPilot writes `<kind>.request` with a random id, posts `com.rootpilot.hud.<kind>`, and the
overlay answers in `<kind>.result.json`, which RootPilot deletes after reading.

| Kind | What it does | Why not ZXTouch |
|---|---|---|
| `ocr` | Vision text recognition of the screen, inside an autorelease pool | ZXTouch's OCR leaks 15–30 MB of SpringBoard memory per read until SpringBoard is killed |
| `clipboard` | set / get the general pasteboard; `type_text` pastes | ZXTouch's keyboard input crashes SpringBoard and Safari on iOS 16 and does nothing on iOS 15 |
| `capture` | save screen frames (`rootpilot capture`) | |

## Kill switch

While a run is active (`com.rootpilot.hud.active`), the overlay stops the agent when you
**lock the phone** or **press the volume buttons 4 times within 2.5 s**. It writes
`/var/mobile/Library/RootPilot/KILL` (source and time), posts `com.rootpilot.hud.killed`, flashes
the edge red and hides the glow. The agent loop checks the file before every model call and
action. Any process posting the Darwin notification `com.rootpilot.hud.kill` does the same
(it can only stop the agent, so it isn't gated). Pressing volume
still changes the volume; the overlay only listens, it doesn't take the buttons.

## Build (Linux)

Needs `clang`, `ld64.lld` (Debian: `lld-21`) and an iPhoneOS SDK, e.g. `iPhoneOS16.5.sdk` from
[theos/sdks](https://github.com/theos/sdks):

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/theos/sdks
(cd sdks && git sparse-checkout set iPhoneOS16.5.sdk)
SDK=$PWD/sdks/iPhoneOS16.5.sdk hud/build.sh
```

This builds an **arm64** slice, which is what A11 and older devices (iPhone X) run. A12+
devices need an arm64e slice as well (not built yet).

## Install (rootful; on rootless prefix the paths with /var/jb)

```sh
scp hud/RootPilotHUD.dylib hud/RootPilotHUD.plist mobile@<device-ip>:/tmp/
# on the device:
D=/Library/MobileSubstrate/DynamicLibraries
sudo cp /tmp/RootPilotHUD.dylib /tmp/RootPilotHUD.plist $D/
sudo chown root:wheel $D/RootPilotHUD.*
sudo ldid -S $D/RootPilotHUD.dylib
sudo sbreload
rootpilot doctor          # "edge glow  installed and loaded in SpringBoard"
```

To remove it: delete the two files and `sudo sbreload`.
