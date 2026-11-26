#!/usr/bin/env bash
# tomo — comando de Tomo (copiloto de reuniones de MIRAI)
#   tomo abrir | empezar "tema" | ayudame | pausar | reanudar | terminar | estado | log | actualizar | desinstalar
PUERTO="${TOMO_PUERTO:-47870}"
URL="http://127.0.0.1:$PUERTO"
DESTINO="${TOMO_DESTINO:-$HOME/.local/share/tomo}"
post() { curl -s -X POST "$URL/api/$1" -H 'Content-Type: application/json' -d "${2:-{\}}"; }
asegurar() {
  curl -s -o /dev/null "$URL/api/estado" && return
  systemctl --user start tomo.service
  for _ in $(seq 1 30); do curl -s -o /dev/null "$URL/api/estado" && return; sleep 0.3; done
}
case "${1:-abrir}" in
  abrir)
    asegurar
    for nav in google-chrome-stable google-chrome chromium chromium-browser brave-browser microsoft-edge-stable vivaldi-stable; do
      if command -v "$nav" >/dev/null; then exec "$nav" --app="$URL/" --class=tomo --window-size=1400,900 >/dev/null 2>&1; fi
    done
    exec xdg-open "$URL/" ;;
  empezar) asegurar; post empezar "$(python3 -c 'import json,sys; print(json.dumps({"tema": " ".join(sys.argv[1:])}))' "${@:2}")" >/dev/null && echo "Escuchando." ;;
  ayudame|pausar|reanudar|terminar) post "$1" >/dev/null && echo "Listo: $1" ;;
  estado) curl -s "$URL/api/estado" | python3 -c '
import json, sys
d = json.load(sys.stdin); s = d.get("sesion")
print(f"Tomo {d.get(\"version\")} · modelos: {\"listos\" if d.get(\"modelos_listos\") else \"falta configurar\"}")
print(f"Reunión: {s[\"tema\"] or \"sin tema\"} · {s[\"estado\"]} · {s[\"minutos\"]} min · {s[\"n_lineas\"]} frases" if s else "Sin reunión en curso")' ;;
  log) journalctl --user -u tomo.service -n 80 --no-pager ;;
  actualizar) bash "$DESTINO/install.sh" ;;
  desinstalar) bash "$DESTINO/install.sh" --desinstalar ;;
  *) sed -n '2,3p' "$0" | sed 's/^# //' ;;
esac
