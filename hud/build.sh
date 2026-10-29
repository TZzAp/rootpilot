#!/bin/sh
# Build RootPilotHUD.dylib (arm64, iOS 14+) on Linux with clang + ld64.lld and an iPhoneOS SDK,
# e.g. iPhoneOS16.5.sdk from https://github.com/theos/sdks. Sign it on the device: ldid -S.
set -e
SDK="${SDK:?set SDK=/path/to/iPhoneOS16.5.sdk}"
LD="${LD:-$(command -v ld64.lld || echo /usr/lib/llvm-21/bin/ld64.lld)}"
cd "$(dirname "$0")"
clang -target arm64-apple-ios14.0 -isysroot "$SDK" -fobjc-arc -O2 -Wall -Wextra \
  -Wno-unused-parameter -dynamiclib --ld-path="$LD" \
  -Wl,-arch,arm64 -Wl,-platform_version,ios,14.0,16.5 -Wl,-syslibroot,"$SDK" \
  -framework Foundation -framework UIKit -framework QuartzCore -framework CoreGraphics \
  -framework Vision \
  -install_name /Library/MobileSubstrate/DynamicLibraries/RootPilotHUD.dylib \
  -o RootPilotHUD.dylib RootPilotHUD.m
echo "built $(pwd)/RootPilotHUD.dylib"
