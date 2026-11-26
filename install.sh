#!/usr/bin/env bash
# Tomo (友) — instalador para Linux (Ubuntu/Debian, Fedora, Arch/CachyOS/Manjaro, openSUSE…).
#
#   curl -fsSL https://raw.githubusercontent.com/sJonathan15/tomo/main/install.sh | bash
#   ./install.sh               instala o actualiza
#   ./install.sh --desinstalar quita Tomo (tus notas y reuniones se quedan)
#
# Qué hace: instala lo del sistema que falte (PipeWire, ImageMagick, captura de pantalla, notificaciones),
# clona el repo en ~/.local/share/tomo, crea un entorno de Python con Whisper, registra el servicio de
# usuario (systemd) y el lanzador «Tomo» en el menú. No toca nada fuera de tu usuario salvo los paquetes.
set -euo pipefail

REPO="${TOMO_REPO:-sJonathan15/tomo}"
DESTINO="${TOMO_DESTINO:-$HOME/.local/share/tomo}"
BIN="$HOME/.local/bin"
UNIDAD="$HOME/.config/systemd/user/tomo.service"
PUERTO="${TOMO_PUERTO:-47870}"

azul() { printf '\033[38;5;39m%s\033[0m\n' "$*"; }
gris() { printf '\033[2m%s\033[0m\n' "$*"; }
falla() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--desinstalar" ]]; then
  systemctl --user disable --now tomo.service 2>/dev/null || true
  rm -f "$UNIDAD" "$BIN/tomo" "$HOME/.local/share/applications/tomo.desktop" "$HOME/.local/share/icons/hicolor/scalable/apps/tomo.svg"
  rm -rf "$DESTINO"
  systemctl --user daemon-reload 2>/dev/null || true
  azul "Tomo desinstalado. Tus reuniones y notas siguen en tu carpeta de Documentos/Tomo y la configuración en ~/.config/tomo."
  exit 0
fi

[[ "$(uname -s)" == "Linux" ]] || falla "Tomo funciona en Linux."
[[ $EUID -ne 0 ]] || falla "Ejecútalo como tu usuario (sin sudo); pedirá la contraseña solo para instalar paquetes."

azul "友  Instalando Tomo · un proyecto de MIRAI"

# ------------------------------------------------------------------ paquetes del sistema
escritorio="$(echo "${XDG_CURRENT_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')"
captura=""
case "$escritorio" in
  *kde*|*plasma*) command -v spectacle >/dev/null || captura="spectacle" ;;
  *gnome*|*unity*|*cinnamon*|*budgie*) captura="" ;;   # GNOME Wayland: portal (python3-gi)
  *hyprland*|*sway*|*niri*|*wlroots*|*river*) captura="grim" ;;
  *) [[ -n "${WAYLAND_DISPLAY:-}" ]] && captura="grim" || captura="maim" ;;
esac

if command -v apt-get >/dev/null; then
  paquetes=(git python3 python3-venv python3-pip pipewire-bin pulseaudio-utils imagemagick libnotify-bin x11-xserver-utils xdg-user-dirs curl python3-gi xdg-desktop-portal)
  [[ -n "$captura" ]] && paquetes+=("$captura")
  faltan=(); for p in "${paquetes[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || faltan+=("$p"); done
  if ((${#faltan[@]})); then gris "sudo apt-get install ${faltan[*]}"; sudo apt-get update -qq && sudo apt-get install -y "${faltan[@]}"; fi
elif command -v dnf >/dev/null; then
  paquetes=(git python3 python3-pip pipewire-utils pulseaudio-utils ImageMagick libnotify xrandr xdg-user-dirs curl python3-gobject xdg-desktop-portal)
  [[ -n "$captura" ]] && paquetes+=("$captura")
  gris "sudo dnf install ${paquetes[*]}"; sudo dnf install -y "${paquetes[@]}"
elif command -v pacman >/dev/null; then
  paquetes=(git python python-pip pipewire pipewire-pulse libpulse imagemagick libnotify xorg-xrandr xdg-user-dirs curl python-gobject xdg-desktop-portal)
  [[ -n "$captura" ]] && paquetes+=("$captura")
  faltan=(); for p in "${paquetes[@]}"; do pacman -Qi "$p" >/dev/null 2>&1 || faltan+=("$p"); done
  if ((${#faltan[@]})); then gris "sudo pacman -S ${faltan[*]}"; sudo pacman -S --needed --noconfirm "${faltan[@]}"; fi
elif command -v zypper >/dev/null; then
  paquetes=(git python3 python3-pip pipewire-tools pulseaudio-utils ImageMagick libnotify-tools xrandr xdg-user-dirs curl python3-gobject xdg-desktop-portal)
  [[ -n "$captura" ]] && paquetes+=("$captura")
  gris "sudo zypper install ${paquetes[*]}"; sudo zypper install -y "${paquetes[@]}"
else
  gris "No reconocí tu gestor de paquetes. Instala a mano: git, python3 (con venv), PipeWire (pw-record, pw-link, pw-dump), pactl, ImageMagick, notify-send y una herramienta de captura ($captura)."
fi

command -v pw-record >/dev/null || falla "Falta PipeWire (pw-record). Tomo necesita PipeWire para oír la reunión (viene por defecto en Ubuntu 22.10+, Fedora 34+, Arch y derivadas)."

# ------------------------------------------------------------------ código
if [[ -d "$DESTINO/.git" ]]; then
  gris "Actualizando $DESTINO"; git -C "$DESTINO" pull --ff-only -q
elif [[ -f "$(dirname "${BASH_SOURCE[0]:-$0}")/tomo/server.py" && -z "${TOMO_DESDE_GITHUB:-}" ]] && [[ "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" != "$DESTINO" ]] && [[ -d "$(dirname "${BASH_SOURCE[0]}")/.git" ]]; then
  gris "Copiando el repo local a $DESTINO"; git clone -q "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" "$DESTINO"
  git -C "$DESTINO" remote set-url origin "https://github.com/$REPO.git"
else
  gris "Descargando https://github.com/$REPO"; git clone -q --depth 1 "https://github.com/$REPO.git" "$DESTINO"
fi

# ------------------------------------------------------------------ Python + Whisper
if [[ ! -x "$DESTINO/.venv/bin/python" ]]; then python3 -m venv "$DESTINO/.venv"; fi
gris "Instalando Whisper (faster-whisper) en $DESTINO/.venv — la primera vez tarda unos minutos"
"$DESTINO/.venv/bin/pip" install -q --upgrade pip
"$DESTINO/.venv/bin/pip" install -q -r "$DESTINO/requirements.txt"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  gris "Tarjeta NVIDIA detectada: añadiendo cuBLAS/cuDNN para usarla"
  "$DESTINO/.venv/bin/pip" install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" || gris "(sin aceleración por GPU; Whisper usará la CPU)"
fi

# ------------------------------------------------------------------ servicio, lanzador y comando
mkdir -p "$(dirname "$UNIDAD")" "$BIN" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cat > "$UNIDAD" <<EOF
[Unit]
Description=Tomo — copiloto de reuniones (MIRAI)
After=graphical-session.target pipewire.service

[Service]
Environment=TOMO_PUERTO=$PUERTO
WorkingDirectory=$DESTINO
ExecStart=$DESTINO/.venv/bin/python -u -m tomo
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
install -m 755 "$DESTINO/tomo.sh" "$BIN/tomo"
install -m 644 "$DESTINO/tomo/web/icono.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/tomo.svg"
cat > "$HOME/.local/share/applications/tomo.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tomo
GenericName=Copiloto de reuniones
Comment=Escucha la reunión, te sopla qué responder y resuelve ejercicios
Exec=$BIN/tomo abrir
Icon=tomo
Terminal=false
Categories=Office;Education;AudioVideo;
StartupWMClass=tomo
EOF
if systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable tomo.service >/dev/null 2>&1 || true
  systemctl --user restart tomo.service
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$PUERTO/api/estado" && break; sleep 0.5; done
  azul "✓ Tomo instalado. Ábrelo desde el menú («Tomo») o con:  tomo abrir"
else
  azul "✓ Tomo instalado (sin servicios de usuario de systemd en este sistema)."
  gris "  Inícialo con:  cd $DESTINO && TOMO_PUERTO=$PUERTO .venv/bin/python -m tomo"
fi
gris "  Primer paso en la app: Ajustes › Modelos de IA (una API gratis o tu modelo local de Ollama)."
case ":$PATH:" in *":$BIN:"*) ;; *) gris "  Nota: agrega $BIN a tu PATH para usar el comando «tomo»." ;; esac
