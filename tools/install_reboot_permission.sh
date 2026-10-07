#!/usr/bin/env bash
# Da permiso al usuario arduino para REINICIAR LA PLACA sin contraseña. Lo usa
# el comando /reboot del dashboard (y su boton), a traves de tools/wifi_helper.py.
#
# Correr UNA vez en la placa (pide la contraseña de sudo):
#     bash tools/install_reboot_permission.sh
#
# Sin esto, /reboot igual hace algo util: reinicia la App (que no necesita
# permisos). Con esto, reinicia la placa entera.
#
# Por que una regla de polkit y no sudoers: el ayudante corre como servicio de
# usuario (sin terminal) y "systemctl reboot" de un usuario normal ya pasa por
# logind, que pregunta a polkit. De fabrica polkit contesta "challenge" (pide
# contraseña); esta regla le dice que si, solo para el usuario arduino y solo
# para REINICIAR. No da ningun otro permiso.
set -eu
RULE=/etc/polkit-1/rules.d/50-robot-bang-reboot.rules
sudo tee "$RULE" >/dev/null <<'RULES'
// Robot BANG: el usuario arduino puede reiniciar la placa sin contraseña.
// Lo usa /reboot del dashboard. Instalado por tools/install_reboot_permission.sh.
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.login1.reboot" ||
         action.id == "org.freedesktop.login1.reboot-multiple-sessions") &&
        subject.user == "arduino") {
        return polkit.Result.YES;
    }
});
RULES
sudo chmod 644 "$RULE"
# polkit relee las reglas solo; se le da un segundo.
sleep 1
echo -n "¿Puede reiniciar ahora? "
busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanReboot
