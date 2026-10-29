# Setting up RootPilot on a device

Tested on two devices:

| Device | iOS | Jailbreak |
|---|---|---|
| iPhone X | 16.7.11 | palera1n **rootful** (Procursus, ElleKit) |
| iPhone 7 Plus | 15.8.3 | Dopamine 2 **rootless** (Procursus, ElleKit) |

The steps are the same for both; where they differ, the rootless variant is noted. On rootless
jailbreaks everything lives under `/var/jb`, which RootPilot detects on its own.

> Read [`SECURITY.md`](SECURITY.md) first. ZXTouch, the touch backend used until the M2 helper
> exists, accepts unauthenticated commands — including root shell commands — from anyone who can
> reach its port. **Step 3 is not optional.**

## 1. Prerequisites

- A jailbroken device you own, with SSH working (OpenSSH from Procursus).
- **Change the default password** for `mobile` (and `root`, if it has one) before anything else:

  ```sh
  passwd            # on the device, as mobile
  ```

- Use SSH keys from your computer instead of passwords:

  ```sh
  ssh-copy-id mobile@<device-ip>      # on your computer
  ```

## 2. Install Python and helper tools

The only Python Procursus offers for iOS 16 rootful is **3.9.9**, which RootPilot supports.

```sh
sudo apt update
sudo apt install python3 uikittools network-cmds ldid
python3 --version        # expect 3.9.x
```

`uikittools` provides `uiopen` (used to open URLs); `network-cmds` provides `netstat` (used by
`rootpilot doctor` to check that ZXTouch is not exposed); `ldid` re-signs the patched ZXTouch.

**Rootless (Dopamine):** check that a tweak loader is installed — `ls /var/jb/usr/lib/TweakInject.dylib`.
If not, `sudo apt install ellekit`. (On the test iPhone 7 Plus the ElleKit repo's signing key was
missing, so apt needed `-o APT::Get::AllowUnauthenticated=true` for that one package.)

## 3. Install ZXTouch — bound to loopback

Upstream ZXTouch listens on **all interfaces** (`#define ADDR "0.0.0.0"` in
`pccontrol/SocketServer.h`), port 6000, with no authentication, and its install script gives
`mobile` passwordless root for `/usr/bin/zxtouchb`. The old repo (`zxtouch.net`) is gone; the
package is on [GitHub releases](https://github.com/xuan32546/IOS13-SimulateTouch/releases).

### 3a. Patch the prebuilt package (what the reference device uses)

Verified on the reference device (iPhone X, iOS 16.7.11, palera1n rootful, ElleKit) on
2026-10-29: touch, swipe, app launch, screen size and text recognition all work.

On your computer (needs `dpkg-deb` and the GitHub CLI):

```sh
gh release download v0.0.7-10 -R xuan32546/IOS13-SimulateTouch -p '*.deb'
python3 scripts/zxtouch_loopback.py com.zjx.ioscontrol_0.0.7-10_iphoneos-arm.deb
scp com.zjx.ioscontrol_0.0.7-10+loopback_iphoneos-arm.deb mobile@<device-ip>:/tmp/
```

The script changes the bind address in all three architecture slices of `pccontrol.dylib` from
`0.0.0.0` to `127.1` (which `inet_addr()` reads as 127.0.0.1 and which fits in the same
bytes), drops the sudoers line from `postinst`, and repacks with root ownership.

On the device: install it, re-sign the patched tweak (patching invalidates its signature), and
restart SpringBoard:

```sh
sudo apt install ldid
sudo dpkg -i /tmp/com.zjx.ioscontrol_0.0.7-10+loopback_iphoneos-arm.deb
sudo ldid -S /Library/MobileSubstrate/DynamicLibraries/pccontrol.dylib
sudo sbreload
```

**Rootless (Dopamine):** build a tweak-only rootless package instead. It installs under
`/var/jb`, and the tweak's substrate links are rewritten to `@rpath/libsubstrate.dylib` with an
rpath of `/var/jb/usr/lib` (verified on the iPhone 7 Plus, iOS 15.8.3):

```sh
python3 scripts/zxtouch_loopback.py --rootless com.zjx.ioscontrol_0.0.7-10_iphoneos-arm.deb
scp com.zjx.ioscontrol_0.0.7-10+loopback+rootless_iphoneos-arm64.deb mobile@<device-ip>:/tmp/
# on the device:
sudo dpkg -i /tmp/com.zjx.ioscontrol_0.0.7-10+loopback+rootless_iphoneos-arm64.deb
sudo ldid -S /var/jb/Library/MobileSubstrate/DynamicLibraries/pccontrol.dylib
sudo sbreload
```

### 3b. Or build from source

With [Theos](https://theos.dev):

```sh
git clone https://github.com/xuan32546/IOS13-SimulateTouch zxtouch && cd zxtouch
sed -i.bak 's/#define ADDR "0.0.0.0"/#define ADDR "127.0.0.1"/' pccontrol/SocketServer.h
make package FINALPACKAGE=1
```

Remove the `sudoers` line from the package's `postinst` before installing.

### 3c. Check it

On the device:

```sh
netstat -an -p tcp | grep '\.6000 '
# good: tcp4  0  0  127.0.0.1.6000   *.*   LISTEN
# bad:  tcp4  0  0  *.6000           *.*   LISTEN   ← reachable from your Wi-Fi
```

And from your computer, `nc -vz <device-ip> 6000` must fail with "connection refused".

> If you install the unpatched package, it **will** listen on all interfaces: only enable it on
> a network you trust, and uninstall it when you're not using it.

### 3d. If the ZXTouch app icon doesn't open

RootPilot only needs the ZXTouch *tweak*. The ZXTouch *app* ships signed for a foreign
developer team, so iOS won't launch it. To use the app anyway, re-sign it ad-hoc:

```sh
scp packaging/zxtouch-app.entitlements mobile@<device-ip>:/tmp/
# on the device:
sudo cp -p /Applications/zxtouch.app/zxtouch /Applications/zxtouch.app/zxtouch.orig
sudo ldid -S/tmp/zxtouch-app.entitlements /Applications/zxtouch.app/zxtouch
sudo uicache -p /Applications/zxtouch.app
```

## 4. Install RootPilot

There is no `.deb` yet (that's M5). Copy the source to the device:

```sh
# on your computer, in the rootpilot checkout
git archive --format=tar --prefix=rootpilot/ HEAD | ssh mobile@<device-ip> 'tar -x -C ~'
```

On the device, run it as a module (no installation needed — it's pure Python):

```sh
cd ~/rootpilot
python3 -m agent --version
```

Optionally add an alias to `~/.zshrc` (or `~/.profile`):

```sh
alias rootpilot='python3 -m agent'
export PYTHONPATH="$HOME/rootpilot"
```

## 5. Configure a provider

Run the setup command and pick a provider (Anthropic, DeepSeek, OpenAI or a local
OpenAI-compatible server). It reads the API key without echoing it and writes
`~/.config/rootpilot/config.json` with mode `0600`:

```sh
rootpilot setup
```

```text
Which model provider should RootPilot use?
  1. Anthropic (Claude)  (current)
  2. DeepSeek
  3. OpenAI
  4. OpenAI-compatible server (local model)
Choose 1-4 [1]: 2
DeepSeek API key, input hidden:
Model [deepseek-flash]:
Saved /var/mobile/.config/rootpilot/config.json (mode 0600): provider deepseek, model deepseek-flash, key stored.
```

Or use environment variables instead (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`,
`OPENAI_API_KEY`). All options are in [`CONFIG.md`](CONFIG.md). RootPilot refuses to read API
keys from a config file other users can read.

## 6. Run `doctor`

```sh
cd ~/rootpilot && python3 -m agent doctor
```

Every line should say `ok`. In particular:

- `backend  zxtouch: 375x812 points; …` — ZXTouch answered on loopback (it works in pixels;
  RootPilot converts to points using the screen scale).
- `zxtouch exposure  loopback only` — if this says **FAIL**, go back to step 3.

## 7. M0 smoke test

```sh
python3 -m agent run "open Settings and tell me the iOS version"
```

Expected: RootPilot opens Settings → General → About, reads the version from the screen (via
ZXTouch's text recognition) and answers **16.7.11**. Each step is printed as it happens; the full
log is in `~/.local/state/rootpilot/runs/<run-id>.jsonl`.

Stop a run at any time with **Ctrl-C**, or from another SSH session:

```sh
touch ~/.local/state/rootpilot/STOP     # remove it again before the next run
```

## 8. Home-screen icon and control page

The control page lets you run RootPilot from the phone itself: type a task, watch each step,
answer Allow/Deny prompts, stop a run, and see recent runs. It is served on `127.0.0.1` only,
behind a secret token.

Keep the server running with launchd (it starts at boot and restarts if it stops).
`rootpilot daemon-plist` writes a plist with the right paths for this device — rootful
(`/Library/LaunchDaemons`) or rootless (`/var/jb/Library/LaunchDaemons`):

```sh
rootpilot daemon-plist > /tmp/rp-serve.plist        # prints the install path on stderr
P=$(rootpilot daemon-plist 2>&1 >/dev/null | sed 's/^# install to //')
sudo cp /tmp/rp-serve.plist $P && sudo chown root:wheel $P && sudo chmod 644 $P
sudo launchctl load $P
rootpilot install-icon        # adds the RootPilot icon to the home screen
```

Run these from the same shell you use for `rootpilot setup`: the daemon uses that shell's home
directory, so it finds the same config and the same secret token as the icon. (On Dopamine the
shell home is `/var/jb/var/mobile`, not `/var/mobile`.)

After updating RootPilot, restart the daemon (`sudo launchctl unload $P && sudo launchctl load $P`);
an open control page reloads itself.

The icon lands on the last free slot of a home-screen page; drag it wherever you like. Tapping
it opens the control page full-screen. `rootpilot install-icon --remove` takes it away.

## 9. Edge glow while the agent is in control (optional)

A SpringBoard overlay draws a glowing edge around the screen while a run is active, and a
ripple where the agent is about to touch. Build and install it as described in
[`hud/README.md`](../hud/README.md). `rootpilot doctor` shows whether it is loaded.

## 10. Screenshots and recordings (optional)

With the overlay installed, `rootpilot capture` saves screen frames as JPEGs (the README media
was made this way). Frames include whatever is on screen, so keep personal data out of shot:

```sh
rootpilot capture --frames 1 --name shot                       # one screenshot
rootpilot capture --frames 200 --interval 0.25 --name run &    # ~50 s at 4 fps, in the background
rootpilot run "open the Calculator app and calculate 128 times 7"
# on your computer: copy the frames over, then
python scripts/frames_to_gif.py frames/ demo.gif --width 300 --speed 3
```

Frames go to `/var/mobile/Library/RootPilot/captures/<name>/` (private to `mobile`).

## Trying it without a device

Everything also runs on a computer against the mock device:

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
rootpilot doctor --backend mock
pytest -m "not device"
```
