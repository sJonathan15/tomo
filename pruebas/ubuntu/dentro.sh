#!/usr/bin/env bash
# Corre dentro del contenedor: bus del sistema, luego una sesión de usuario con GNOME sin pantalla.
if [ "$(id -u)" = 0 ]; then
  rm -rf /run/systemd/seats
  mkdir -p /run/dbus /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix && dbus-daemon --system --fork
  chmod 777 /salida
  exec runuser -u prueba -- env LANG=C.UTF-8 bash "$0"
fi
export XDG_RUNTIME_DIR=/tmp/run && mkdir -p -m 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland XDG_SESSION_DESKTOP=ubuntu
exec dbus-run-session -- bash /pruebas/sesion.sh
