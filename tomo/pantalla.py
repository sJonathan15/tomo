"""Capturas de pantalla y lista de monitores en cualquier escritorio Linux.

Captura (la primera herramienta que exista y funcione):
  KDE Plasma → spectacle · Hyprland/Sway/niri/wlroots → grim · GNOME → gnome-screenshot ·
  X11 → maim / import / scrot. GNOME en Wayland → compartir pantalla por el portal (como Zoom).
Las imágenes se procesan con Pillow (ImageMagick solo de respaldo).
Monitores: kscreen-doctor (KDE) · hyprctl · swaymsg · niri msg · xrandr (X11 y XWayland en GNOME).
Con un monitor elegido se recorta la captura de todo el escritorio a esa pantalla (como «compartir una
pantalla» en Zoom). grim puede capturar un monitor directamente.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

try:                     # Pillow (pip) para recortar, reducir y comparar capturas; ImageMagick queda de respaldo
    from PIL import Image, ImageStat
except ImportError:      # pragma: no cover
    Image = None

_lock = threading.Lock()


def entorno():
    """El servicio de systemd a veces arranca antes que el escritorio y no hereda WAYLAND_DISPLAY/DISPLAY."""
    env = dict(os.environ)
    run = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    env.setdefault("XDG_RUNTIME_DIR", str(run))
    if not env.get("WAYLAND_DISPLAY"):
        socks = sorted(x.name for x in run.glob("wayland-[0-9]") if x.is_socket())
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    if not env.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
        env["DISPLAY"] = ":0"
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")
    if not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hypr = sorted((run / "hypr").glob("*")) if (run / "hypr").is_dir() else []
        if hypr:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = hypr[-1].name
    if not env.get("SWAYSOCK"):
        socks = sorted(run.glob("sway-ipc.*.sock"))
        if socks:
            env["SWAYSOCK"] = str(socks[-1])
    if not env.get("NIRI_SOCKET"):
        socks = sorted(run.glob("niri.*.sock"))
        if socks:
            env["NIRI_SOCKET"] = str(socks[-1])
    if env.get("WAYLAND_DISPLAY"):
        env["QT_QPA_PLATFORM"] = "wayland"
    return env


def escritorio():
    env = entorno()
    d = (env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or "").lower()
    if "kde" in d or "plasma" in d:
        return "kde"
    if "gnome" in d or "unity" in d or "cinnamon" in d or "budgie" in d:
        return "gnome"
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in d:
        return "hyprland"
    if env.get("SWAYSOCK") or "sway" in d:
        return "sway"
    if env.get("NIRI_SOCKET") or "niri" in d:
        return "niri"
    return "wayland" if env.get("WAYLAND_DISPLAY") else "x11"


def _correr(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=entorno())


# ------------------------------------------------------------------ monitores
def monitores():
    """[{nombre, x, y, w, h, px, interna, etiqueta}] en coordenadas lógicas, de izquierda a derecha."""
    out = []
    try:
        if shutil.which("kscreen-doctor") and escritorio() == "kde":
            for o in json.loads(_correr(["kscreen-doctor", "-j"]).stdout).get("outputs", []):
                if not o.get("enabled"):
                    continue
                modo = next((m for m in o.get("modes", []) if m.get("id") == o.get("currentModeId")), None)
                if not modo:
                    continue
                esc = float(o.get("scale") or 1)
                w, h = modo["size"]["width"], modo["size"]["height"]
                if o.get("rotation") in (2, 8):
                    w, h = h, w
                out.append(dict(nombre=o["name"], x=o["pos"]["x"], y=o["pos"]["y"], w=round(w / esc), h=round(h / esc), px=f"{w}×{h}"))
        elif shutil.which("hyprctl") and escritorio() == "hyprland":
            for o in json.loads(_correr(["hyprctl", "monitors", "-j"]).stdout):
                esc = float(o.get("scale") or 1)
                out.append(dict(nombre=o["name"], x=o["x"], y=o["y"], w=round(o["width"] / esc), h=round(o["height"] / esc),
                                px=f"{o['width']}×{o['height']}"))
        elif shutil.which("swaymsg") and escritorio() == "sway":
            for o in json.loads(_correr(["swaymsg", "-t", "get_outputs", "-r"]).stdout):
                if o.get("active"):
                    r, m = o["rect"], o.get("current_mode") or {}
                    out.append(dict(nombre=o["name"], x=r["x"], y=r["y"], w=r["width"], h=r["height"],
                                    px=f"{m.get('width', r['width'])}×{m.get('height', r['height'])}"))
        elif shutil.which("niri") and escritorio() == "niri":
            for nombre, o in json.loads(_correr(["niri", "msg", "-j", "outputs"]).stdout).items():
                lg = o.get("logical")
                if lg:
                    out.append(dict(nombre=nombre, x=lg["x"], y=lg["y"], w=lg["width"], h=lg["height"], px=f"{lg['width']}×{lg['height']}"))
        if not out and shutil.which("xrandr"):
            for l in _correr(["xrandr", "--listmonitors"]).stdout.splitlines()[1:]:
                m = re.search(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)\s+(\S+)$", l.strip())
                if m:
                    w, h, x, y, nombre = int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5]
                    out.append(dict(nombre=nombre, x=x, y=y, w=w, h=h, px=f"{w}×{h}"))
    except Exception:
        out = []
    out.sort(key=lambda m: (m["x"], m["y"]))
    for i, m in enumerate(out):
        m["interna"] = m["nombre"].startswith(("eDP", "LVDS", "DSI"))
        lado = "izquierda" if i == 0 else "derecha" if i == len(out) - 1 else "centro"
        m["etiqueta"] = f"Pantalla {i + 1} · {'portátil' if m['interna'] else 'externa'}" + (f" ({lado})" if len(out) > 1 else "")
    return out


# ------------------------------------------------------------------ captura
PORTAL = Path(__file__).with_name("portal_captura.py")


def hay_portal():
    """Portal de capturas disponible: Python del sistema con gi + xdg-desktop-portal en la sesión."""
    if not Path("/usr/bin/python3").exists():
        return False
    try:
        r = subprocess.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Gio','2.0')"], capture_output=True, timeout=5)
        if r.returncode:
            return False
        r = subprocess.run(["busctl", "--user", "status", "org.freedesktop.portal.Desktop"], capture_output=True, timeout=5, env=entorno())
        return r.returncode == 0 or bool(shutil.which("gdbus"))
    except Exception:
        return False


def herramientas():
    """Qué herramientas de captura hay, en el orden que conviene a este escritorio.
    GNOME en Wayland no deja capturar a programas sueltos (gnome-screenshot sale negro): ahí va primero el portal."""
    esc = escritorio()
    wayland = bool(entorno().get("WAYLAND_DISPLAY"))
    orden = {"kde": ["spectacle", "grim", "portal", "import"],
             "gnome": (["portal", "gnome-screenshot"] if wayland else ["gnome-screenshot", "maim", "import", "portal"]),
             "hyprland": ["grim", "portal"], "sway": ["grim", "portal"], "niri": ["grim", "portal"],
             "wayland": ["grim", "portal", "spectacle", "gnome-screenshot"],
             "x11": ["maim", "import", "scrot", "spectacle", "gnome-screenshot"]}[esc]
    return [h for h in orden if (hay_portal() if h == "portal" else shutil.which(h))]


def _capturar(png: Path, herramienta, salida=""):
    if herramienta == "portal":
        r = subprocess.run(["/usr/bin/python3", str(PORTAL), str(png)], timeout=100, env=entorno(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode or not png.exists():
            raise RuntimeError("el portal no entregó la captura (¿se negó el permiso?)")
        return _no_negra(png)
    cmd = {"spectacle": ["spectacle", "-b", "-n", "-f", "-o", str(png)],
           "grim": ["grim"] + (["-o", salida] if salida else []) + [str(png)],
           "gnome-screenshot": ["gnome-screenshot", "-f", str(png)],
           "maim": ["maim", str(png)],
           "import": ["import", "-window", "root", str(png)],
           "scrot": ["scrot", "-o", str(png)]}[herramienta]
    subprocess.run(cmd, timeout=15, check=True, env=entorno(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not png.exists() or png.stat().st_size < 1000:
        raise RuntimeError(f"{herramienta} no dejó imagen")
    _no_negra(png)


def _no_negra(png: Path):
    """En Wayland algunas herramientas «funcionan» pero entregan la pantalla en negro: eso cuenta como fallo."""
    try:
        if Image:
            with Image.open(png) as im:
                g = im.convert("L")
                g.thumbnail((64, 64))
                media = ImageStat.Stat(g).mean[0] / 255
        else:
            cmd = _magick() + [str(png), "-resize", "64x", "-colorspace", "gray", "-format", "%[fx:mean]", "info:"]
            media = float(subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout or 1)
    except Exception:
        return
    if media < 0.004:
        png.unlink(missing_ok=True)
        raise RuntimeError("la captura salió negra")


def _magick():
    return ["magick"] if shutil.which("magick") else ["convert"]


def _identificar(png):
    if Image:
        with Image.open(png) as im:
            return im.size
    cmd = ["magick", "identify"] if shutil.which("magick") else ["identify"]
    return tuple(map(int, subprocess.run(cmd + ["-format", "%w %h", str(png)], capture_output=True, text=True,
                                         timeout=10).stdout.split()))


def recorte(png: Path, nombre: str):
    mons = monitores()
    m = next((x for x in mons if x["nombre"] == nombre), None)
    if not m:
        return None
    x0, y0 = min(x["x"] for x in mons), min(x["y"] for x in mons)
    ancho = max(x["x"] + x["w"] for x in mons) - x0
    alto = max(x["y"] + x["h"] for x in mons) - y0
    iw, ih = _identificar(png)
    fx, fy = iw / ancho, ih / alto
    return f"{round(m['w'] * fx)}x{round(m['h'] * fy)}+{round((m['x'] - x0) * fx)}+{round((m['y'] - y0) * fy)}"


# ------------------------------------------------------------------ compartir pantalla (ScreenCast) en GNOME/Wayland
COMPARTIR = Path(__file__).with_name("portal_compartir.py")
_RUN = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "tomo"
CUADRO = _RUN / "cuadro.jpg"
TOKEN = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tomo" / "compartir.token"
_comp = {"proc": None, "estado": "", "detalle": "", "desde": 0.0}


def usar_compartir():
    """GNOME en Wayland (Ubuntu): capturar con el portal hace destello y sonido y toma todas las pantallas.
    Ahí se comparte pantalla como Zoom: se elige una vez la pantalla o la ventana y llegan cuadros sin destello."""
    if os.environ.get("TOMO_CAPTURA") == "compartir":
        return True
    if os.environ.get("TOMO_CAPTURA"):
        return False
    esc = escritorio()
    return bool(entorno().get("WAYLAND_DISPLAY")) and (esc == "gnome" or (esc == "wayland" and not shutil.which("grim")))


def gstreamer_listo():
    r = subprocess.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None);"
                        "import sys; sys.exit(0 if Gst.ElementFactory.find('pipewiresrc') and Gst.ElementFactory.find('jpegenc') else 1)"],
                       capture_output=True, timeout=10, env=entorno())
    return r.returncode == 0


def compartir(elegir=False):
    """Arranca (o reinicia con «elegir» para volver a preguntar qué compartir) el ayudante de ScreenCast."""
    p = _comp["proc"]
    if p and p.poll() is None and not elegir:
        return _comp
    if p and p.poll() is None:
        p.terminate()
    _RUN.mkdir(parents=True, exist_ok=True)
    CUADRO.unlink(missing_ok=True)
    cmd = ["/usr/bin/python3", str(COMPARTIR), str(CUADRO), str(TOKEN)] + (["--elegir"] if elegir else [])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=entorno())
    _comp.update(proc=p, estado="eligiendo", detalle="", desde=time.time())

    def leer():
        for linea in p.stdout:
            try:
                d = json.loads(linea)
            except Exception:
                continue
            _comp.update(estado=d.get("estado", ""), detalle=d.get("detalle", ""))
        if _comp["proc"] is p:
            _comp["estado"] = _comp["estado"] if _comp["estado"] in ("cancelado", "error") else "detenido"
    threading.Thread(target=leer, daemon=True).start()
    return _comp


def estado_compartir():
    p = _comp["proc"]
    return {"activo": usar_compartir(), "estado": _comp["estado"] if p else "", "detalle": _comp["detalle"],
            "recordado": TOKEN.exists()}


def _desde_compartir(png: Path):
    c = compartir()
    if c["estado"] == "cancelado":                   # el usuario dijo que no: no volver a abrir la ventana sola
        raise RuntimeError("no se eligió qué compartir: pulsa «Elegir qué compartir» en Tomo")
    if c["estado"] in ("error", "detenido") and TOKEN.exists() and time.time() - c["desde"] > 5:
        compartir()                                  # se cayó: reintentar (con lo recordado no vuelve a preguntar)
    for _ in range(30):                              # el primer cuadro tarda un poco
        if CUADRO.exists():
            shutil.copyfile(CUADRO, png)             # magick reconoce el JPEG por su contenido
            return png
        if _comp["estado"] in ("cancelado", "error"):
            break
        time.sleep(0.2)
    raise RuntimeError("esperando que elijas qué compartir" if _comp["estado"] == "eligiendo"
                       else f"compartir pantalla: {_comp['estado'] or 'sin cuadros'} {_comp['detalle']}".strip())


_ultima_buena = {"herramienta": None}


def capturar(png: Path, monitor=""):
    """Deja en `png` la pantalla elegida (o todo el escritorio). Lanza excepción si nada funciona."""
    with _lock:
        png.parent.mkdir(parents=True, exist_ok=True)
        if usar_compartir():
            return _desde_compartir(png)            # ya es solo lo que el usuario eligió compartir
        opciones = herramientas()
        if _ultima_buena["herramienta"] in opciones:
            opciones.remove(_ultima_buena["herramienta"])
            opciones.insert(0, _ultima_buena["herramienta"])
        if not opciones:
            raise RuntimeError("no hay herramienta de captura: instala spectacle, grim, gnome-screenshot o maim")
        ultimo = None
        for h in opciones:
            try:
                directo = h == "grim" and monitor
                _capturar(png, h, monitor if directo else "")
                _ultima_buena["herramienta"] = h
                if monitor and not directo:
                    geo = recorte(png, monitor)
                    if geo:
                        _recortar(png, geo)
                return png
            except Exception as e:
                ultimo = e
        raise RuntimeError(f"no pude capturar la pantalla: {ultimo}")


def _recortar(png: Path, geo: str):
    w, h, x, y = map(int, re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", geo).groups())
    if Image:
        with Image.open(png) as im:
            im.load()
            fmt = im.format or "PNG"
            im.crop((x, y, x + w, y + h)).save(png, fmt)
        return
    subprocess.run(_magick() + [str(png), "-crop", geo, "+repage", str(png)], timeout=10, check=True)


def jpeg(png: Path, ancho=1280, calidad=72):
    """Bytes JPEG reducidos de una captura."""
    if Image:
        import io
        with Image.open(png) as im:
            im = im.convert("RGB")
            if im.width > ancho:
                im = im.resize((ancho, round(im.height * ancho / im.width)), Image.LANCZOS)
            b = io.BytesIO()
            im.save(b, "JPEG", quality=calidad)
            return b.getvalue()
    return subprocess.run(_magick() + [str(png), "-resize", f"{ancho}x>", "-quality", str(calidad), "jpg:-"],
                          timeout=10, check=True, capture_output=True).stdout


def huella(png: Path):
    """32×18 en grises, para saber si cambió la diapositiva sin llamar a ningún modelo."""
    if Image:
        with Image.open(png) as im:
            return im.convert("L").resize((32, 18), Image.BILINEAR).tobytes()
    return subprocess.run(_magick() + [str(png), "-resize", "32x18!", "-colorspace", "gray", "gray:-"],
                          timeout=10, check=True, capture_output=True).stdout


_vivo = {"t": 0.0, "jpg": None, "monitor": None}


def vivo(tmp: Path, monitor=""):
    """Lo que Tomo ve ahora: a lo sumo una captura cada 3 s aunque varias ventanas lo pidan.
    Compartiendo pantalla se devuelve el último cuadro (no cuesta nada). Si la única forma de capturar es el portal
    de capturas (destello y sonido), la vista en vivo NO toma capturas nuevas: muestra la última que se analizó."""
    if usar_compartir():
        try:
            capturar(tmp, monitor)
            return jpeg(tmp, 1100)
        finally:
            tmp.unlink(missing_ok=True)
    if (herramientas() or [""])[0] == "portal":
        return _vivo["jpg"]
    if time.time() - _vivo["t"] > 3 or _vivo["jpg"] is None or _vivo["monitor"] != monitor:
        try:
            capturar(tmp, monitor)
            _vivo.update(t=time.time(), jpg=jpeg(tmp, 1100), monitor=monitor)
        finally:
            tmp.unlink(missing_ok=True)
    return _vivo["jpg"]


def miniatura(tmp: Path, monitor):
    try:
        capturar(tmp, monitor)
        return jpeg(tmp, 480)
    finally:
        tmp.unlink(missing_ok=True)
