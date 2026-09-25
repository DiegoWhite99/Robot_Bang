#!/usr/bin/env bash
# Le quita el Bluetooth al PipeWire de la pantalla de login (usuario lightdm)
# para que lo maneje el del usuario arduino, que es el que ve la App.
#
# Por que: lightdm corre su propio PipeWire + WirePlumber y, como tiene el
# seat0 activo, se queda con los perfiles de audio Bluetooth (A2DP y manos
# libres). La bocina conecta y suena, pero la App no la ve ni puede usar su
# microfono (en el log de WirePlumber de arduino: "RegisterProfile() failed:
# NotPermitted" / "listen(): Address already in use").
#
# Correr UNA vez en la placa:   sudo bash tools/fix_bt_lightdm.sh
set -eu

if [ "$(id -u)" != 0 ]; then
  echo "Hay que correrlo con sudo:  sudo bash $0" >&2
  exit 1
fi

CONF_DIR=/var/lib/lightdm/.config/wireplumber/wireplumber.conf.d
mkdir -p "$CONF_DIR"
cat > "$CONF_DIR/50-sin-bluetooth.conf" <<'EOF'
# Robot BANG: el Bluetooth lo maneja el PipeWire del usuario arduino.
wireplumber.profiles = {
  main = {
    monitor.bluez = disabled
    monitor.bluez-midi = disabled
  }
}
EOF
chown -R lightdm:lightdm /var/lib/lightdm/.config

LIGHTDM_UID=$(id -u lightdm)
echo "Reiniciando WirePlumber de lightdm..."
sudo -u lightdm XDG_RUNTIME_DIR=/run/user/$LIGHTDM_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$LIGHTDM_UID/bus \
  systemctl --user restart wireplumber || pkill -u lightdm wireplumber || true
sleep 2

ARDUINO_UID=$(id -u arduino)
echo "Reiniciando WirePlumber de arduino..."
sudo -u arduino XDG_RUNTIME_DIR=/run/user/$ARDUINO_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ARDUINO_UID/bus \
  systemctl --user restart wireplumber
sleep 3

echo "Listo. Ahora reconecta la bocina (en la terminal del dashboard: /add_bt y /bt_connect <n>)."
