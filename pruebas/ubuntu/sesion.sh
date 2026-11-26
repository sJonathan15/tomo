#!/usr/bin/env bash
# La prueba en sí: instalar, oír una «reunión de Zoom» hablada, mirar la pantalla, terminar y revisar la nota.
S=/salida; U=http://127.0.0.1:47870
r() { echo "$*" | tee -a $S/resumen.txt; }
j() { curl -s "$U$1"; }
p() { curl -s -X POST "$U$1" -H 'Content-Type: application/json' -d "${2:-{\}}"; }

pipewire > $S/pipewire.log 2>&1 & sleep 1
wireplumber > $S/wireplumber.log 2>&1 & pipewire-pulse > /dev/null 2>&1 & sleep 3
pactl load-module module-null-sink sink_name=parlantes sink_properties=device.description=Parlantes > /dev/null
pactl set-default-sink parlantes
gsettings set org.gnome.shell welcome-dialog-last-shown-version 999 2>/dev/null
gnome-shell --headless --wayland --virtual-monitor 1280x800 --unsafe-mode > $S/shell.log 2>&1 &
for i in $(seq 40); do [ -S "$XDG_RUNTIME_DIR/wayland-0" ] && break; sleep 1; done
export WAYLAND_DISPLAY=wayland-0
r "GNOME: $(gnome-shell --version) · wayland: $([ -S $XDG_RUNTIME_DIR/wayland-0 ] && echo sí || echo NO)"

git config --global --add safe.directory '*'
cd /repo && TOMO_PUERTO=47870 bash install.sh > $S/install.log 2>&1
r "instalador: código $? · $(tail -3 $S/install.log | tr '\n' ' ')"

cd ~/.local/share/tomo
HF_HOME=/hf HF_HUB_OFFLINE=1 TOMO_PUERTO=47870 TOMO_SIN_ACTUALIZAR=1 .venv/bin/python -u -m tomo > $S/tomo.log 2>&1 &
for i in $(seq 30); do curl -s -o /dev/null $U/api/estado && break; sleep 1; done
j /api/sistema > $S/sistema.json
r "sistema: $(python3 -c 'import json; d=json.load(open("/salida/sistema.json")); print("escritorio", d["escritorio"], "· audio", d["motor_audio"], "· capturas", d["capturas"], "· gstreamer", d["gstreamer"], "· faltan", [x["que"] for x in d["faltan"]], "·", d["comando"])')"

# una «reunión de Zoom»: espeak habla por un stream que se llama zoom
espeak-ng -v es -s 150 -w /tmp/voz.wav "Buenos días a todos. Hoy hablamos de agentes de inteligencia artificial. ¿Alguien sabe qué es un servidor MCP? Escriban su respuesta en el chat." 
p /api/ajustes '{"modelo":"small","pantalla":true}' > /dev/null
p /api/empezar '{"tema":"Prueba en Ubuntu"}' > /dev/null
for i in $(seq 90); do [ "$(j /api/estado | python3 -c 'import json,sys; print((json.load(sys.stdin)["sesion"] or {}).get("estado"))')" = escuchando ] && break; sleep 1; done
r "reunión: $(j /api/estado | python3 -c 'import json,sys; s=json.load(sys.stdin)["sesion"]; print(s["estado"], s["fuente"], s.get("error"))')"
for k in 1 2; do pw-play -P '{ application.name = "zoom" node.name = "zoom" }' /tmp/voz.wav; sleep 2; done
sleep 12
r "oído: $(j /api/estado | python3 -c 'import json,sys; s=json.load(sys.stdin)["sesion"]; print("apps oídas", s["apps_oidas"], "modo", s["modo_oido"], "|", " / ".join(l["texto"] for l in s["lineas"]))')"

# pantalla: en GNOME Wayland se comparte como Zoom (aparece el diálogo del sistema)
p /api/compartir '{}' > /dev/null; sleep 4
gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot \
  --method org.gnome.Shell.Screenshot.Screenshot false false $S/pantalla-dialogo.png > /dev/null 2>&1
python3 /pruebas/teclas.py Return >> $S/teclas.log 2>&1; sleep 4
gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot \
  --method org.gnome.Shell.Screenshot.Screenshot false false $S/pantalla-despues.png > /dev/null 2>&1
r "compartir: $(j /api/estado | python3 -c 'import json,sys; print(json.load(sys.stdin)["compartir"])')"
curl -s -o $S/vivo.jpg -w "vivo.jpg %{http_code} %{size_download} bytes\n" $U/api/vivo.jpg | tee -a $S/resumen.txt

p /api/terminar > /dev/null
for i in $(seq 120); do [ "$(j /api/estado | python3 -c 'import json,sys; print(json.load(sys.stdin)["sesion"]["estado"])')" != resumiendo ] && break; sleep 1; done
r "final: $(j /api/estado | python3 -c 'import json,sys; s=json.load(sys.stdin)["sesion"]; print(s["estado"], s["nota"], s.get("error"))')"
r "notas: $(j /api/notas | python3 -c 'import json,sys; print([(n["titulo"], n["sin_ia"]) for n in json.load(sys.stdin)["notas"]])')"
r "abrir fuera: $(j "/api/nota?nombre=Indice" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("abrir"))')"
r "seguridad: origen ajeno → $(curl -s -o /dev/null -w '%{http_code}' -X POST $U/api/ajustes -H 'Content-Type: application/json' -H 'Origin: https://malo.com' -d '{}')"
r "oídos sobrantes: $(pactl list short modules | grep -c tomo_oido)"
[ -n "${ESPERAR:-}" ] && sleep "$ESPERAR"
grep -iE "error|Traceback" $S/tomo.log | head -5 | sed 's/^/tomo.log: /' | tee -a $S/resumen.txt
