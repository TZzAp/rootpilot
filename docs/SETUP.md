# Setting up RootPilot on a device

This guide targets the reference device: **iPhone X, iOS 16.7.11, palera1n rootful** (Procursus
bootstrap, ElleKit). Rootless jailbreaks (Dopamine, palera1n rootless) work the same way; the
only difference is that packages live under `/var/jb`, which RootPilot detects on its own.

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

Create `~/.config/rootpilot/config.json` **with mode 0600** (RootPilot refuses to read API keys
from a file other users can read):

```sh
mkdir -p ~/.config/rootpilot
umask 077
cat > ~/.config/rootpilot/config.json <<'EOF'
{
  "provider": "anthropic",
  "anthropic_api_key": "sk-ant-...",
  "backend": "zxtouch"
}
EOF
chmod 600 ~/.config/rootpilot/config.json
```

Or use environment variables instead of the file (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). All
options are in [`CONFIG.md`](CONFIG.md).

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

## Trying it without a device

Everything also runs on a computer against the mock device:

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
rootpilot doctor --backend mock
pytest -m "not device"
```
