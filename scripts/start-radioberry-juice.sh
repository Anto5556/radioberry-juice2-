#!/usr/bin/env bash
# Launch the Radioberry Juice gateway (official FTDI D2XX build + loader patch).
# Exits 0 when the FT2232H is not plugged in, so systemd does not restart-loop.
set -u
cd "$(dirname "$0")" || exit 1

for f in /sys/bus/usb/devices/*/idVendor; do
    [ "$(cat "$f" 2>/dev/null)" = "0403" ] || continue
    [ "$(cat "${f%idVendor}idProduct" 2>/dev/null)" = "6010" ] || continue
    exec stdbuf -oL -eL ./radioberry-juice
done

echo "No FT2232H device 0403:6010 found on USB — nothing to do." >&2
exit 0
