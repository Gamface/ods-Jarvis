#!/bin/bash
# Jarvis one-time host setup (needs sudo). Run it yourself:
#   sudo bash ~/Documents/ODS/jarvis/jarvis-host-setup.sh
#
# What it does:
#   1. apt install python3-venv (for Jarvis's Python services) and ydotool
#      (mouse/keyboard input on Wayland).
#   2. Creates a ydotoold system service whose control socket only the
#      invoking user (not other users) can use.
# Nothing else is changed.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0" >&2
    exit 1
fi
TARGET_USER="${SUDO_USER:?run via sudo from your own account}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_GID="$(id -g "$TARGET_USER")"
PYVER="$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

echo "==> Installing packages: python${PYVER}-venv ydotool"
apt-get update -q
apt-get install -y "python${PYVER}-venv" ydotool

echo "==> Creating ydotoold service (socket owned by ${TARGET_USER})"
cat > /etc/systemd/system/ydotoold.service <<EOF
[Unit]
Description=ydotool input daemon for Jarvis (socket owned by ${TARGET_USER})

[Service]
ExecStart=/usr/bin/ydotoold --socket-path=/run/ydotoold/socket --socket-perm=0600 --socket-own=${TARGET_UID}:${TARGET_GID}
RuntimeDirectory=ydotoold
RuntimeDirectoryMode=0755
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ydotoold.service
systemctl --no-pager --lines=0 status ydotoold.service

echo
echo "Done. Log out and back in (or reboot) so Docker group membership takes effect,"
echo "then tell Claude to continue."
