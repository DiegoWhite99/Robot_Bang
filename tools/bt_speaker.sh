#!/usr/bin/env bash
# Bocina Bluetooth para el robot. Se corre en la PLACA (no en el contenedor):
#
#   bash tools/bt_speaker.sh              # desbloquea, prende y conecta la bocina ya emparejada
#   bash tools/bt_speaker.sh scan         # busca bocinas cercanas (ponla en modo emparejar)
#   bash tools/bt_speaker.sh pair <MAC>   # empareja, confia y conecta una bocina nueva
#
# Una vez conectada, la App la detecta sola (PipeWire nodo bluez_output.*) y
# la voz sale por ahi en unos segundos, sin reiniciar nada. El microfono
# sigue siendo el del headset USB.
#
# No hace falta sudo: el usuario arduino esta en el grupo netdev, que puede
# escribir /dev/rfkill.
set -u

unblock() {
  local blocked=0 r
  for r in /sys/class/rfkill/rfkill*; do
    [ "$(cat "$r/type")" = bluetooth ] && [ "$(cat "$r/soft")" = 1 ] && blocked=1
  done
  if [ "$blocked" = 1 ]; then
    # struct rfkill_event { u32 idx; u8 type; u8 op; u8 soft; u8 hard }:
    # type 2 = bluetooth, op 3 = CHANGE_ALL, soft 0 = desbloqueado.
    python3 -c "import struct; open('/dev/rfkill','wb').write(struct.pack('=IBBBB',0,2,3,0,0))" \
      && echo "rfkill: Bluetooth desbloqueado"
    sleep 1
  fi
  bluetoothctl power on >/dev/null 2>&1 || true
  bluetoothctl show | grep -E "Powered|PowerState"
}

connect_known() {
  local found=0
  while read -r _ mac name; do
    found=1
    echo "Conectando a $name ($mac)..."
    if timeout 20 bluetoothctl connect "$mac" | grep -q "Connection successful"; then
      echo "✅ Conectada: $name"
      return 0
    fi
    echo "   no respondio (¿esta prendida y cerca?)"
  done < <(bluetoothctl devices Paired)
  [ "$found" = 0 ] && echo "No hay bocinas emparejadas: usa '$0 scan' y luego '$0 pair <MAC>'."
  return 1
}

case "${1:-}" in
  scan)
    unblock
    echo "Buscando 15 s... (pon la bocina en modo emparejar)"
    timeout 15 bluetoothctl --timeout 15 scan on >/dev/null 2>&1
    bluetoothctl devices
    ;;
  pair)
    mac="${2:?Uso: $0 pair <MAC>}"
    unblock
    timeout 30 bluetoothctl pair "$mac" && bluetoothctl trust "$mac" && timeout 20 bluetoothctl connect "$mac"
    ;;
  *)
    unblock
    connect_known
    ;;
esac

wpctl status | sed -n '/Audio/,/Sources/p' | grep -iE "bluez|Sinks|\*" || true
