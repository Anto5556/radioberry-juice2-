#!/usr/bin/env bash
# Build and install the Radioberry Juice gateway on an x86_64/arm Linux PC:
#   - fetches pa3gsb/Radioberry-2.x (juice/firmware-extended, official FTDI D2XX build)
#   - applies patches/0001-juice-gateware-loader-linux-d2xx.patch
#   - installs to ~/radioberry-juice/gateway with fpga=CL016|CL025
#   - installs and starts a systemd *user* service
#
# usage: scripts/install.sh [CL025|CL016]
set -euo pipefail

FPGA="${1:-CL025}"
case "$FPGA" in CL016|CL025) ;; *) echo "usage: $0 [CL025|CL016]" >&2; exit 1 ;; esac

HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$HOME/radioberry-juice}"
SRC="$WORK/Radioberry-2.x"
DST="$WORK/gateway"
UPSTREAM_COMMIT="${UPSTREAM_COMMIT:-a9c5139265b4e739ef93e9da6514ff949cb89446}"

mkdir -p "$WORK"
if [ ! -d "$SRC/.git" ]; then
    git clone https://github.com/pa3gsb/Radioberry-2.x.git "$SRC"
fi
git -C "$SRC" fetch --depth=50 origin "$UPSTREAM_COMMIT" 2>/dev/null || true
git -C "$SRC" checkout -q "$UPSTREAM_COMMIT" -- juice/firmware-extended

cd "$SRC"
if git apply --check "$HERE/patches/0001-juice-gateware-loader-linux-d2xx.patch" 2>/dev/null; then
    git apply "$HERE/patches/0001-juice-gateware-loader-linux-d2xx.patch"
    echo "patch applied"
elif git apply --reverse --check "$HERE/patches/0001-juice-gateware-loader-linux-d2xx.patch" 2>/dev/null; then
    echo "patch already applied"
else
    echo "patch does not apply to this upstream version" >&2; exit 1
fi

cd "$SRC/juice/firmware-extended"
make -f linux-Makefile -j"$(nproc)"
ARCH_DIR="$(ls -d dist/linux-* | head -1)"

systemctl --user stop radioberry-juice.service 2>/dev/null || true
mkdir -p "$DST"
cp -r "$ARCH_DIR/radioberry-juice" "$ARCH_DIR/lib" "$ARCH_DIR/gateware" "$DST/"
if [ ! -f "$DST/radioberry.props" ]; then
    printf 'call=CALL\nlocator=LOC\nfpga=%s\n' "$FPGA" > "$DST/radioberry.props"
else
    sed -i "s/^fpga=.*/fpga=$FPGA/" "$DST/radioberry.props"
    grep -q '^fpga=' "$DST/radioberry.props" || echo "fpga=$FPGA" >> "$DST/radioberry.props"
fi
install -m 755 "$HERE/scripts/start-radioberry-juice.sh" "$DST/"

mkdir -p "$HOME/.config/systemd/user"
U="$HOME/.config/systemd/user/radioberry-juice.service"
[ -f "$U" ] && cp "$U" "$U.bak.$(date +%Y%m%d%H%M%S)"
install -m 644 "$HERE/systemd/radioberry-juice.service" "$U"
systemctl --user daemon-reload
systemctl --user enable --now radioberry-juice.service

cat <<EOF

Installed to $DST (fpga=$FPGA).
USB access: the FT2232H (0403:6010) must be readable by your user and not
bound to ftdi_sio — see README "USB access". Check the log:
  tail -f $DST/gateway.log     # expect "FPGA gateware activated."
EOF
