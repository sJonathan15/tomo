# Ubuntu 24.04 con GNOME Shell sin pantalla (Wayland), PipeWire y el portal: para probar Tomo como en un Ubuntu real.
#   docker build -t tomo-ubuntu pruebas/ubuntu && pruebas/ubuntu/probar.sh
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      gnome-shell mutter dbus dbus-user-session gsettings-desktop-schemas libgl1-mesa-dri \
      pipewire pipewire-pulse wireplumber pipewire-bin pulseaudio-utils xdg-desktop-portal xdg-desktop-portal-gnome \
      sudo git curl ca-certificates python3 python3-venv python3-pip espeak-ng fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash prueba && echo 'prueba ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/prueba
