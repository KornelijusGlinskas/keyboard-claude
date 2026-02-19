#!/bin/bash
# Build custom firmware for Work Louder Micro with per-key LED control.
#
# Usage:
#   ./firmware/build.sh vial      # Try VIAL build (may fail on ATmega32u4)
#   ./firmware/build.sh raw_hid   # Raw HID build (lightweight, recommended)
#   ./firmware/build.sh flash     # Flash the last successful build
#
# Prerequisites:
#   brew install qmk/qmk/qmk
#   qmk setup  (first time only — clones qmk_firmware)

set -euo pipefail
cd "$(dirname "$0")/.."

FIRMWARE_DIR="firmware"
QMK_HOME="${QMK_HOME:-$HOME/qmk_firmware}"
VIAL_QMK_HOME="${VIAL_QMK_HOME:-$HOME/vial-qmk}"
KB_PATH="keyboards/work_louder/micro"

# ── Helpers ─────────────────────────────────────

red()   { echo -e "\033[0;31m$*\033[0m"; }
green() { echo -e "\033[0;32m$*\033[0m"; }
bold()  { echo -e "\033[1m$*\033[0m"; }

check_qmk() {
    if ! command -v qmk &>/dev/null; then
        red "QMK CLI not found. Install with:"
        echo "  brew install qmk/qmk/qmk"
        echo "  qmk setup"
        exit 1
    fi
}

# ── VIAL build ──────────────────────────────────

build_vial() {
    bold "Building VIAL firmware..."
    echo

    if [ ! -d "$VIAL_QMK_HOME" ]; then
        bold "Cloning vial-qmk (this takes a while)..."
        git clone --depth 1 https://github.com/vial-kb/vial-qmk.git "$VIAL_QMK_HOME"
        cd "$VIAL_QMK_HOME"
        make git-submodule
        cd -
    fi

    # Copy keymap files
    KEYMAP_DIR="$VIAL_QMK_HOME/$KB_PATH/keymaps/vial"
    mkdir -p "$KEYMAP_DIR"
    cp "$FIRMWARE_DIR/vial/config.h"   "$KEYMAP_DIR/"
    cp "$FIRMWARE_DIR/vial/keymap.c"   "$KEYMAP_DIR/"
    cp "$FIRMWARE_DIR/vial/rules.mk"   "$KEYMAP_DIR/"
    cp "$FIRMWARE_DIR/vial/vial.json"  "$KEYMAP_DIR/"
    green "Keymap files copied to $KEYMAP_DIR"

    # Build
    cd "$VIAL_QMK_HOME"
    echo
    bold "Running: make work_louder/micro:vial"
    echo
    if make work_louder/micro:vial; then
        green "VIAL build succeeded!"
        echo "Firmware: $VIAL_QMK_HOME/work_louder_micro_vial.hex"
        cp work_louder_micro_vial.hex "$OLDPWD/$FIRMWARE_DIR/"
        green "Copied to $FIRMWARE_DIR/work_louder_micro_vial.hex"
    else
        red "VIAL build failed (likely memory overflow on ATmega32u4)."
        echo "Try the raw_hid build instead:"
        echo "  ./firmware/build.sh raw_hid"
        exit 1
    fi
}

# ── Raw HID build ───────────────────────────────

build_raw_hid() {
    bold "Building Raw HID firmware..."
    echo

    check_qmk

    if [ ! -d "$QMK_HOME" ]; then
        bold "Setting up QMK..."
        qmk setup -y
    fi

    # Copy keymap files
    KEYMAP_DIR="$QMK_HOME/$KB_PATH/keymaps/raw_hid"
    mkdir -p "$KEYMAP_DIR"
    cp "$FIRMWARE_DIR/raw_hid/config.h"   "$KEYMAP_DIR/"
    cp "$FIRMWARE_DIR/raw_hid/keymap.c"   "$KEYMAP_DIR/"
    cp "$FIRMWARE_DIR/raw_hid/rules.mk"   "$KEYMAP_DIR/"
    green "Keymap files copied to $KEYMAP_DIR"

    # Build
    cd "$QMK_HOME"
    echo
    bold "Running: qmk compile -kb work_louder/micro -km raw_hid"
    echo
    if qmk compile -kb work_louder/micro -km raw_hid; then
        green "Raw HID build succeeded!"
        HEX_FILE=$(find . -maxdepth 1 -name "work_louder_micro_raw_hid.*" -newer "$KEYMAP_DIR/keymap.c" | head -1)
        if [ -n "$HEX_FILE" ]; then
            cp "$HEX_FILE" "$OLDPWD/$FIRMWARE_DIR/"
            green "Copied to $FIRMWARE_DIR/$(basename "$HEX_FILE")"
        fi
    else
        red "Build failed."
        exit 1
    fi
}

# ── Flash ───────────────────────────────────────

reboot_to_bootloader() {
    # Try to send Raw HID command 0x09 (with magic bytes) to reboot into bootloader
    python3 -c "
import hid, sys
try:
    dev = hid.device()
    for desc in hid.enumerate(0x574C, 0xE6E3):
        if desc['usage_page'] == 0xFF60 and desc['usage'] == 0x61:
            dev.open_path(desc['path'])
            msg = bytes([0x09, 0xB0, 0x07]) + b'\x00' * 29
            dev.write(b'\x00' + msg)
            dev.close()
            sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

flash_firmware() {
    bold "Flashing firmware..."
    echo

    HEX=$(ls -t "$FIRMWARE_DIR"/*.hex 2>/dev/null | head -1)
    if [ -z "$HEX" ]; then
        red "No .hex file found in $FIRMWARE_DIR/"
        echo "Build first with: ./firmware/build.sh raw_hid"
        exit 1
    fi

    bold "Flashing: $HEX"

    # Try software reboot into bootloader first
    if reboot_to_bootloader; then
        green "Sent bootloader reboot via USB"
        sleep 2
    else
        echo "Could not reach keyboard via USB — manual bootloader entry needed:"
        echo "  Hold top-left encoder + plug USB"
        echo
    fi

    echo "Waiting for bootloader..."
    check_qmk
    qmk flash -kb work_louder/micro "$HEX"
}

# ── Main ────────────────────────────────────────

case "${1:-}" in
    vial)    build_vial ;;
    raw_hid) build_raw_hid ;;
    flash)   flash_firmware ;;
    *)
        echo "Usage: ./firmware/build.sh <command>"
        echo
        echo "Commands:"
        echo "  vial      Build VIAL firmware (may not fit ATmega32u4)"
        echo "  raw_hid   Build Raw HID firmware (recommended)"
        echo "  flash     Flash the last built firmware"
        ;;
esac
