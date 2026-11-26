#!/usr/bin/env bash
# Instala y prueba Tomo dentro de un Ubuntu 24.04 con GNOME (Docker). Resultados en pruebas/ubuntu/salida/.
# Prueba el commit actual (HEAD): haz commit antes. HF_CACHE = caché de Hugging Face con faster-whisper-small.
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$AQUI/../.." && pwd)"
mkdir -p "$AQUI/salida" && rm -rf "$AQUI/salida/"*
docker run --rm --network host -u 0 \
  -v "$REPO:/repo:ro" -v "$AQUI:/pruebas:ro" -v "$AQUI/salida:/salida" \
  ${HF_CACHE:+-v "$HF_CACHE:/hf:ro"} \
  tomo-ubuntu bash /pruebas/dentro.sh
cat "$AQUI/salida/resumen.txt"
