#!/usr/bin/env bash
# Instala tools/bt_helper.py como servicio de usuario (arranca solo con la
# placa, sin sudo). Correr una vez en la placa:  bash tools/install_bt_helper.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/bang-bt-helper.service" <<EOF
[Unit]
Description=Robot BANG - ayudante de Bluetooth para el dashboard
After=default.target

[Service]
ExecStart=/usr/bin/python3 $HERE/bt_helper.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now bang-bt-helper.service
systemctl --user --no-pager status bang-bt-helper.service | head -5
