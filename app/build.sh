#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
APP_NAME="KeyboardClaude"
BUILD_DIR="$PROJECT_DIR/build"
APP_DIR="$BUILD_DIR/$APP_NAME.app"

echo "Building $APP_NAME..."

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"

# Detect architecture
ARCH=$(uname -m)
TARGET="${ARCH}-apple-macos14.0"

swiftc "$SCRIPT_DIR/main.swift" \
    -o "$APP_DIR/Contents/MacOS/$APP_NAME" \
    -framework Cocoa \
    -framework SwiftUI \
    -target "$TARGET" \
    -O

cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Keyboard Claude</string>
    <key>CFBundleIdentifier</key>
    <string>dev.kornelijus.keyboard-claude</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>KeyboardClaude</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST

echo "Built: $APP_DIR"
echo "Run:   open \"$APP_DIR\""
