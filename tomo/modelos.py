"""Modelos de IA de Tomo: APIs compatibles con OpenAI (en cadena) + modelos locales con Ollama.

- Cada llamada pide un «uso»: "vision" (leer la pantalla) o "texto" (responder, resolver, notas).
- Se prueban en orden los proveedores con clave y, al final, el modelo local. Si uno se queda sin cupo
  (400/401/403/404/429) se salta 10 minutos y se pasa al siguiente: Tomo nunca se queda sin ojos.
- Las claves viven en ~/.config/tomo/modelos.json con permisos 600 y nunca se devuelven completas a la app.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tomo"
ARCHIVO = CONFIG / "modelos.json"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA

# Plantillas: el usuario solo pega su clave. Los modelos se pueden cambiar (botón «Ver modelos» lista los de su cuenta).
PLANTILLAS = {
    "gemini": {"nombre": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "texto": "gemini-flash-latest", "vision": "gemini-flash-latest",
               "clave_en": "https://aistudio.google.com/apikey", "gratis": True},
    "nvidia": {"nombre": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1",
               "texto": "nvidia/nemotron-3.5-super-vl-120b-a12b", "vision": "nvidia/nemotron-3.5-super-vl-120b-a12b",
               "clave_en": "https://build.nvidia.com", "gratis": True},
    "openrouter": {"nombre": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "texto": "openrouter/auto", "vision": "openrouter/auto",
                   "clave_en": "https://openrouter.ai/keys", "gratis": False},
    "groq": {"nombre": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "texto": "", "vision": "", "clave_en": "https://console.groq.com/keys", "gratis": True},
    "openai": {"nombre": "OpenAI", "base_url": "https://api.openai.com/v1", "texto": "gpt-4o-mini", "vision": "gpt-4o-mini",
               "clave_en": "https://platform.openai.com/api-keys", "gratis": False},
    "otro": {"nombre": "Otro (compatible con OpenAI)", "base_url": "", "texto": "", "vision": "", "clave_en": "", "gratis": False},
}

# Modelos locales recomendados según la memoria de video (GB). Todos leen imágenes y escriben en español.
RECOMENDADOS = [
    (10, "qwen3-vl:8b-instruct", "≈ 6 GB · el que mejor lee diapositivas"),
    (5, "qwen3-vl:4b-instruct", "≈ 3,3 GB · buen equilibrio"),
    (0, "qwen3-vl:2b-instruct", "≈ 1,1 GB · pequeño y funcional; cabe junto a Whisper"),
]

_lock = threading.Lock()
_agotado = {}                     # "proveedor:modelo" → hasta cuándo no se intenta
_cache_local = {"t": 0.0, "modelos": []}
DESCARGA = {"modelo": "", "estado": "", "avance": 0.0, "error": ""}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[modelos]", *a, flush=True)


# ------------------------------------------------------------------ configuración
def leer():
    try:
        d = json.loads(ARCHIVO.read_text())
    except Exception:
        d = {}
    d.setdefault("proveedores", [])
    d.setdefault("local", {"activo": True, "vision": "", "texto": ""})
    return d


def guardar(d):
    CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(ARCHIVO)


def publico():
    """Lo que ve la app: sin claves (solo los 4 últimos caracteres)."""
    d = leer()
    provs = [{**{k: v for k, v in p.items() if k != "api_key"},
              "clave": ("…" + p["api_key"][-4:]) if p.get("api_key") else "",
              "agotado": any(t > time.time() for k, t in _agotado.items() if k.startswith(p["id"] + ":"))}
             for p in d["proveedores"]]
    return {"proveedores": provs, "local": d["local"], "plantillas": PLANTILLAS, "ollama": estado_local(),
            "descarga": DESCARGA, "listo": hay_algo(d)}


def hay_algo(d=None):
    d = d or leer()
    return any(p.get("api_key") and p.get("activo", True) for p in d["proveedores"]) or bool(elegir_local("vision"))


def guardar_proveedor(p):
    """Crea o actualiza un proveedor. Si la clave llega vacía se conserva la anterior."""
    d = leer()
    plantilla = PLANTILLAS.get(p.get("tipo") or "otro", PLANTILLAS["otro"])
    pid = p.get("id") or f"{p.get('tipo', 'otro')}-{int(time.time())}"
    viejo = next((x for x in d["proveedores"] if x["id"] == pid), {})
    nuevo = {"id": pid, "tipo": p.get("tipo") or viejo.get("tipo") or "otro",
             "nombre": p.get("nombre") or viejo.get("nombre") or plantilla["nombre"],
             "base_url": (p.get("base_url") or viejo.get("base_url") or plantilla["base_url"]).rstrip("/"),
             "api_key": p.get("api_key") or viejo.get("api_key", ""),
             "texto": p.get("texto", viejo.get("texto", plantilla["texto"])),
             "vision": p.get("vision", viejo.get("vision", plantilla["vision"])),
             "activo": p.get("activo", viejo.get("activo", True))}
    if viejo:
        d["proveedores"][d["proveedores"].index(viejo)] = nuevo
    else:
        d["proveedores"].append(nuevo)
    guardar(d)
    for k in [k for k in _agotado if k.startswith(pid + ":")]:
        _agotado.pop(k)
    return pid


def borrar_proveedor(pid):
    d = leer()
    d["proveedores"] = [p for p in d["proveedores"] if p["id"] != pid]
    guardar(d)


def mover_proveedor(pid, paso):
    d = leer()
    ids = [p["id"] for p in d["proveedores"]]
    if pid in ids:
        i = ids.index(pid)
        j = max(0, min(len(ids) - 1, i + paso))
        d["proveedores"][i], d["proveedores"][j] = d["proveedores"][j], d["proveedores"][i]
        guardar(d)


def guardar_local(cambios):
    d = leer()
    d["local"].update({k: v for k, v in cambios.items() if k in ("activo", "vision", "texto")})
    guardar(d)


# ------------------------------------------------------------------ modelos locales (Ollama)
def vram_gb():
    if shutil.which("nvidia-smi"):
        try:
            mb = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=5).stdout.split()
            return round(int(mb[0]) / 1024, 1) if mb else 0
        except Exception:
            pass
    return 0


def ram_gb():
    try:
        kb = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1])
        return round(kb / 1024 / 1024, 1)
    except Exception:
        return 0


def _get(url, timeout=4):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


def _post(url, cuerpo, timeout=10):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(cuerpo).encode(),
                                                                   {"Content-Type": "application/json"}), timeout=timeout))


def modelos_locales(forzar=False):
    """[{nombre, tamano_gb, vision}] de Ollama (cache 5 min). Vacío si Ollama no está."""
    if not forzar and time.time() - _cache_local["t"] < 300:
        return _cache_local["modelos"]
    out = []
    try:
        for m in _get(f"{OLLAMA}/api/tags").get("models", []):
            try:
                caps = _post(f"{OLLAMA}/api/show", {"model": m["name"]}).get("capabilities") or []
            except Exception:
                caps = []
            if "embedding" in caps and "completion" not in caps:
                continue
            out.append({"nombre": m["name"], "tamano_gb": round(m.get("size", 0) / 1e9, 1), "vision": "vision" in caps})
    except Exception:
        out = []
    out.sort(key=lambda m: (not m["vision"], "ocr" in m["nombre"], m["tamano_gb"]))
    _cache_local.update(t=time.time(), modelos=out)
    return out


def recomendado():
    v = vram_gb()
    # con tarjeta: dejar ≈2 GB para Whisper; sin tarjeta NVIDIA corre en CPU: mejor uno pequeño
    memoria = (v - 2) if v else ram_gb() / 4
    for minimo, nombre, nota in RECOMENDADOS:
        if memoria >= minimo:
            return {"nombre": nombre, "nota": nota, "vram_gb": v, "ram_gb": ram_gb()}
    return {"nombre": RECOMENDADOS[-1][1], "nota": RECOMENDADOS[-1][2], "vram_gb": v, "ram_gb": ram_gb()}


def estado_local():
    instalado = shutil.which("ollama") is not None
    try:
        _get(f"{OLLAMA}/api/version", timeout=2)
        corriendo = True
    except Exception:
        corriendo = False
    modelos = modelos_locales() if corriendo else []
    return {"instalado": instalado or corriendo, "corriendo": corriendo, "url": OLLAMA, "modelos": modelos,
            "recomendado": recomendado()}


def descargar(nombre):
    """`ollama pull` en segundo plano con avance (lo lee la app)."""
    if DESCARGA["estado"] == "descargando":
        return
    DESCARGA.update(modelo=nombre, estado="descargando", avance=0.0, error="")

    def correr():
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/pull", json.dumps({"model": nombre, "stream": True}).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3600) as r:
                for linea in r:
                    d = json.loads(linea or b"{}")
                    if d.get("error"):
                        raise RuntimeError(d["error"])
                    if d.get("total"):
                        DESCARGA["avance"] = round(d.get("completed", 0) / d["total"], 3)
            DESCARGA.update(estado="listo", avance=1.0)
            modelos_locales(forzar=True)
            d = leer()
            if not d["local"].get("vision"):
                guardar_local({"vision": nombre, "texto": d["local"].get("texto") or nombre})
        except Exception as e:
            DESCARGA.update(estado="error", error=str(e))
    threading.Thread(target=correr, daemon=True).start()


def elegir_local(uso):
    d = leer()
    if not d["local"].get("activo", True):
        return ""
    elegido = d["local"].get(uso) or d["local"].get("vision")
    if elegido:
        return elegido
    locales = modelos_locales()
    if uso == "vision":
        return next((m["nombre"] for m in locales if m["vision"] and "ocr" not in m["nombre"]), "")
    return next((m["nombre"] for m in locales if "ocr" not in m["nombre"]), "")


# ------------------------------------------------------------------ llamar
def con_imagen(mensajes):
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in mensajes)


def _llamar(url, clave, modelo, mensajes, max_tokens, timeout, extra=None):
    pedido = {"model": modelo, "messages": mensajes, "max_tokens": max_tokens, **(extra or {})}
    cab = {"Content-Type": "application/json"}
    if clave:
        cab["Authorization"] = f"Bearer {clave}"
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, json.dumps(pedido).encode(), cab), timeout=timeout))
    return (d["choices"][0]["message"].get("content") or "").strip()


def cadena(uso):
    """[(etiqueta, url, clave, modelo, es_local)] en el orden en que se prueban."""
    d = leer()
    out = []
    for p in d["proveedores"]:
        modelo = p.get(uso) or p.get("texto") or p.get("vision")
        if p.get("activo", True) and p.get("api_key") and modelo and p.get("base_url"):
            out.append((f"{p['id']}:{modelo}", f"{p['base_url']}/chat/completions", p["api_key"], modelo, False))
    local = elegir_local(uso)
    if local:
        out.append((f"local:{local}", f"{OLLAMA}/v1/chat/completions", "", local, True))
    return out


def llamar(uso, mensajes, max_tokens=3000, timeout=60):
    """Prueba cada modelo de la cadena hasta que uno responda. uso = "vision" | "texto"."""
    if con_imagen(mensajes):
        uso = "vision"
    opciones = cadena(uso)
    if not opciones:
        raise RuntimeError("no hay modelos configurados: abre Ajustes › Modelos de IA")
    ultimo = None
    for etiqueta, url, clave, modelo, local in opciones:
        if _agotado.get(etiqueta, 0) > time.time() and etiqueta != opciones[-1][0]:
            continue
        try:
            extra = {"keep_alive": "2m"} if local else {}
            r = _llamar(url, clave, modelo, mensajes, max_tokens, max(timeout, 180) if local else timeout, extra)
            if not r:
                raise RuntimeError("respuesta vacía")
            return r
        except Exception as e:
            ultimo = e
            codigo = getattr(e, "code", 0)
            with _lock:
                _agotado[etiqueta] = time.time() + (600 if codigo in (400, 401, 403, 404, 429) else 60)
            log(f"{etiqueta} falló ({codigo or e}); pruebo el siguiente")
    raise ultimo or RuntimeError("ningún modelo respondió")


def probar(pid):
    """Prueba rápida de un proveedor (texto). Devuelve (ok, mensaje)."""
    p = next((x for x in leer()["proveedores"] if x["id"] == pid), None)
    if not p:
        return False, "no existe"
    try:
        r = _llamar(f"{p['base_url']}/chat/completions", p["api_key"], p.get("texto") or p.get("vision"),
                    [{"role": "user", "content": "Responde solo: listo"}], 20, 30)
        _agotado.pop(f"{pid}:{p.get('texto')}", None)
        return True, r[:60] or "respondió"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}"
    except Exception as e:
        return False, str(e)


def listar_remotos(pid):
    """Modelos disponibles en la cuenta del proveedor (GET /models)."""
    p = next((x for x in leer()["proveedores"] if x["id"] == pid), None)
    if not p:
        return []
    req = urllib.request.Request(f"{p['base_url']}/models", headers={"Authorization": f"Bearer {p['api_key']}"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=15))
        return sorted(m.get("id", "").replace("models/", "") for m in d.get("data", []))[:400]
    except Exception:
        return []
