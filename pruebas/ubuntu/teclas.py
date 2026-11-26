#!/usr/bin/python3
"""Pulsa teclas en GNOME sin pantalla con la API RemoteDesktop de Mutter (para aceptar diálogos en las pruebas)."""
import sys, time
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
KEYS = {"Return": 0xff0d, "Tab": 0xff09, "space": 0x20, "Right": 0xff53, "Down": 0xff54}
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
ruta = bus.call_sync("org.gnome.Mutter.RemoteDesktop", "/org/gnome/Mutter/RemoteDesktop", "org.gnome.Mutter.RemoteDesktop",
                     "CreateSession", None, GLib.VariantType("(o)"), 0, -1, None).unpack()[0]
ses = lambda m, a=None: bus.call_sync("org.gnome.Mutter.RemoteDesktop", ruta, "org.gnome.Mutter.RemoteDesktop.Session", m, a, None, 0, -1, None)
ses("Start")
for k in sys.argv[1:]:
    for abajo in (True, False):
        ses("NotifyKeyboardKeysym", GLib.Variant("(ub)", (KEYS.get(k, 0), abajo)))
        time.sleep(0.08)
    time.sleep(0.4)
ses("Stop")
print("teclas:", sys.argv[1:])
