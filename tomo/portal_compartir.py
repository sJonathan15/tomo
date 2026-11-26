#!/usr/bin/python3
"""Compartir pantalla con el portal del escritorio (org.freedesktop.portal.ScreenCast) — como lo hace Zoom.

La primera vez el escritorio pregunta qué compartir (una pantalla o una ventana, p. ej. la de Zoom); se guarda un
«token de restauración» y las siguientes veces no pregunta. Mientras corre, deja el último cuadro en un JPEG
(sin destello, sin sonido de cámara, solo lo elegido). Corre con el Python del sistema (python3-gi + GStreamer).

Uso: portal_compartir.py <cuadro.jpg> <token.txt> [--elegir]
  --elegir  olvida lo elegido y vuelve a preguntar.
Escribe en stdout una línea JSON por evento: {"estado": "compartiendo"|"error"|"cancelado", ...}
"""
import json
import os
import random
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

CUADRO = Path(sys.argv[1])
TOKEN = Path(sys.argv[2])
if "--elegir" in sys.argv:
    TOKEN.unlink(missing_ok=True)

Gst.init(None)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
emisor = bus.get_unique_name()[1:].replace(".", "_")
bucle = GLib.MainLoop()
estado = {"sesion": None, "pipeline": None}
IFACE = "org.freedesktop.portal.ScreenCast"


def decir(**d):
    print(json.dumps(d, ensure_ascii=False), flush=True)


def pedir(metodo, args, firma, siguiente):
    """Llama a un método del portal y, cuando llega su Response, sigue con `siguiente(resultados)`."""
    token = f"tomo{random.randint(0, 1 << 30)}"
    ruta = f"/org/freedesktop/portal/desktop/request/{emisor}/{token}"
    sub = []

    def respuesta(_c, _e, _r, _i, _s, params, *_):
        bus.signal_unsubscribe(sub[0])
        codigo, res = params.unpack()
        if codigo != 0:
            decir(estado="cancelado" if codigo == 1 else "error", paso=metodo)
            bucle.quit()
            return
        siguiente(res)

    sub.append(bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response",
                                    ruta, None, Gio.DBusSignalFlags.NO_MATCH_RULE, respuesta))
    opciones = args[-1]
    opciones["handle_token"] = GLib.Variant("s", token)
    bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", IFACE, metodo,
                  GLib.Variant(firma, tuple(args)), None, Gio.DBusCallFlags.NONE, -1, None)


def crear():
    pedir("CreateSession", [{"session_handle_token": GLib.Variant("s", f"tomos{random.randint(0, 1 << 30)}")}],
          "(a{sv})", elegir)


def elegir(res):
    estado["sesion"] = res["session_handle"]
    opc = {"types": GLib.Variant("u", 1 | 2),          # pantalla o ventana
           "multiple": GLib.Variant("b", False),
           "cursor_mode": GLib.Variant("u", 1),         # sin el cursor
           "persist_mode": GLib.Variant("u", 2)}        # recordar hasta que se revoque
    if TOKEN.exists() and TOKEN.read_text().strip():
        opc["restore_token"] = GLib.Variant("s", TOKEN.read_text().strip())
    pedir("SelectSources", [estado["sesion"], opc], "(oa{sv})", empezar)


def empezar(_res):
    pedir("Start", [estado["sesion"], "", {}], "(osa{sv})", conectar)


def conectar(res):
    if res.get("restore_token"):
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(res["restore_token"])
    flujos = res.get("streams") or []
    if not flujos:
        decir(estado="error", paso="Start", detalle="sin flujos")
        return bucle.quit()
    nodo, props = flujos[0]
    r, fds = bus.call_with_unix_fd_list_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", IFACE,
                                             "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (estado["sesion"], {})),
                                             None, Gio.DBusCallFlags.NONE, -1, None, None)
    fd = fds.get(r.unpack()[0])
    tam = props.get("size") or (0, 0)
    p = Gst.parse_launch(
        f"pipewiresrc fd={fd} path={nodo} always-copy=true do-timestamp=true ! "
        "video/x-raw ! videoconvert ! video/x-raw,format=I420 ! jpegenc quality=80 ! "
        "appsink name=salida emit-signals=true max-buffers=1 drop=true sync=false")
    p.get_by_name("salida").connect("new-sample", guardar)
    p.get_bus().add_signal_watch()
    p.get_bus().connect("message::error", lambda _b, m: (decir(estado="error", detalle=str(m.parse_error()[0])), bucle.quit()))
    p.set_state(Gst.State.PLAYING)
    estado["pipeline"] = p
    decir(estado="compartiendo", tipo=props.get("source_type", 0), ancho=tam[0], alto=tam[1])


_ultimo = {"t": 0}


def guardar(sink):
    muestra = sink.emit("pull-sample")
    ahora = GLib.get_monotonic_time()
    if ahora - _ultimo["t"] < 1_000_000:        # a lo sumo un cuadro por segundo al disco
        return Gst.FlowReturn.OK
    _ultimo["t"] = ahora
    buf = muestra.get_buffer()
    ok, info = buf.map(Gst.MapFlags.READ)
    if ok:
        tmp = CUADRO.with_suffix(".tmp")
        tmp.write_bytes(bytes(info.data))
        os.replace(tmp, CUADRO)
        buf.unmap(info)
    return Gst.FlowReturn.OK


crear()
try:
    bucle.run()
finally:
    if estado["pipeline"]:
        estado["pipeline"].set_state(Gst.State.NULL)
