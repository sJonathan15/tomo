#!/usr/bin/python3
"""Captura de pantalla por el portal de escritorio (org.freedesktop.portal.Screenshot).

Es la forma que permiten GNOME y otros escritorios Wayland: la primera vez el sistema pregunta si Tomo puede
capturar la pantalla; después lo recuerda. Corre con el Python del sistema (necesita python3-gi).
Uso: portal_captura.py <destino.png>   → código 0 si dejó la imagen.
"""
import os
import random
import shutil
import sys
import urllib.parse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

destino = sys.argv[1]
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
emisor = bus.get_unique_name()[1:].replace(".", "_")
token = f"tomo{random.randint(0, 1 << 30)}"
ruta = f"/org/freedesktop/portal/desktop/request/{emisor}/{token}"
bucle = GLib.MainLoop()
res = {}


def respuesta(_con, _emisor, _ruta, _iface, _senal, params, *_):
    codigo, resultados = params.unpack()
    res.update(codigo=codigo, uri=resultados.get("uri"))
    bucle.quit()


bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response", ruta, None,
                     Gio.DBusSignalFlags.NO_MATCH_RULE, respuesta)
bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Screenshot",
              "Screenshot", GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                                          "interactive": GLib.Variant("b", False)})),
              None, Gio.DBusCallFlags.NONE, -1, None)
GLib.timeout_add_seconds(90, bucle.quit)      # la primera vez el usuario tiene que pulsar «Permitir»
bucle.run()
if res.get("codigo") != 0 or not res.get("uri"):
    sys.exit(1)
origen = urllib.parse.unquote(urllib.parse.urlparse(res["uri"]).path)
shutil.move(origen, destino)                   # el portal la deja en ~/Imágenes: se mueve, no queda basura
sys.exit(0 if os.path.exists(destino) else 1)
