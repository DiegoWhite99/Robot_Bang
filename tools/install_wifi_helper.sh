#!/usr/bin/env bash
# Instala tools/wifi_helper.py como servicio de usuario (arranca solo con la
# placa, sin sudo). Correr una vez en la placa: bash tools/install_wifi_helper.sh
#
# Mismo patron que install_bt_helper.sh. El usuario arduino ya puede usar
# nmcli sin sudo (esta en el grupo netdev), asi que el servicio no necesita
# privilegios extra.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/bang-wifi-helper.service" <<EOF
[Unit]
Description=Robot BANG - ayudante de WiFi para el dashboard
After=default.target

[Service]
ExecStart=/usr/bin/python3 $HERE/wifi_helper.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now bang-wifi-helper.service
systemctl --user --no-pager status bang-wifi-helper.service | head -5
