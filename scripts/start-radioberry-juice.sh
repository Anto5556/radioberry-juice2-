#!/usr/bin/env bash
# Launch the Radioberry Juice gateway (official FTDI D2XX build + patches).
#
# Waits for the FT2232H (0403:6010) to be plugged in, so the service needs no
# udev help: when the board is unplugged the gateway exits, systemd restarts
# this script, and it simply waits here until the board is back.
set -u
cd "$(dirname "$0")" || exit 1

board_present() {
    for f in /sys/bus/usb/devices/*/idVendor; do
        [ "$(cat "$f" 2>/dev/null)" = "0403" ] || continue
        [ "$(cat "${f%idVendor}idProduct" 2>/dev/null)" = "6010" ] && return 0
    done
    return 1
}

if ! board_present; then
    echo "Waiting for the Radioberry Juice board (FT2232H 0403:6010) to be plugged in..." >&2
    until board_present; do sleep 1; done
    sleep 2   # let udev release the interfaces from ftdi_sio first
fi

exec stdbuf -oL -eL ./radioberry-juice
