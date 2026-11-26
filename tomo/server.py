#!/usr/bin/env python3
"""Tomo (友, «compañero») — tu copiloto de reuniones. Parte de MIRAI.

Escucha la reunión (Zoom, Teams, Meet…) y, si quieres, tu micrófono; transcribe con Whisper local cortando
por pausas reales (Silero VAD); cuando oye una pregunta mira la pantalla y sugiere qué responder; resuelve
ejercicios (texto o captura) con lo que se dijo en clase; y al terminar escribe una nota en Markdown
(compatible con Obsidian). Sirve la app (web/) y una API JSON en 127.0.0.1.

Modelos: APIs compatibles con OpenAI en cadena + Ollama local (modelos.py). Captura: pantalla.py.
"""
import base64
import difflib
import gc
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

from . import modelos, pantalla
from . import whisper as wh
from .whisper import fiable, limpiar

VERSION = "0.3.0"
BASE = Path(__file__).resolve().parent
PUERTO = int(os.environ.get("TOMO_PUERTO", "47870"))
TASA = 16000
WEB = BASE / "web"


def carpeta_documentos():
    try:
        d = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=3).stdout.strip()
        if d and d != str(Path.home()):
            return Path(d)
    except Exception:
        pass
    return Path.home() / "Documents"


RAIZ = Path(os.environ.get("TOMO_DATOS") or carpeta_documentos() / "Tomo")      # una carpeta por reunión
CONFIG = modelos.CONFIG
AJUSTES_F = CONFIG / "ajustes.json"
AJUSTES_DEF = {"microfono": False, "pantalla": True, "globitos": True, "modelo": "auto", "idioma": "es",
               "fuente": "app",          # app: directo de Zoom/Teams… (no importa el volumen) · salida: lo que suena
               "apps": "zoom|teams|webex|skype|slack|discord", "monitor": "",
               "notas": "",              # carpeta de notas ("" = <RAIZ>/Notas); puede ser una bóveda de Obsidian
               "nombre": "", "rol": "", "proyectos": "",
               "tema": "sistema", "bienvenida": False}     # tema: sistema|claro|noche · bienvenida: ya vio el asistente inicial
VAD = VadOptions(threshold=0.5, min_silence_duration_ms=300, speech_pad_ms=150)
MAX_TROZO = 15            # s: más largo que esto se corta en la pausa (o el punto más callado)
PREGUNTA = re.compile(
    r"\?|¿|\b(qué|cómo|cuál(es)?|cuándo|dónde|por qué|quién(es)?|alguien|"
    r"opinan|creen|saben|conocen|pueden decirme|alguna (duda|pregunta)|levanten la mano|"
    r"escriban en el chat|respondan|what|how|why|which|who|anyone|any questions)\b", re.I)


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def leer_ajustes():
    try:
        return {**AJUSTES_DEF, **json.loads(AJUSTES_F.read_text())}
    except Exception:
        return dict(AJUSTES_DEF)


def guardar_ajustes(a):
    AJUSTES_F.parent.mkdir(parents=True, exist_ok=True)
    AJUSTES_F.write_text(json.dumps(a, indent=2, ensure_ascii=False))


def carpeta_notas():
    n = leer_ajustes().get("notas") or ""
    return Path(os.path.expanduser(n)) if n else RAIZ / "Notas"


def perfil():
    """Quién es el usuario, para que las respuestas suenen a él (se llena en Ajustes › Tu perfil)."""
    a = leer_ajustes()
    partes = [x for x in (a.get("nombre"), a.get("rol")) if x]
    return ("SOBRE EL USUARIO: " + "; ".join(partes)) if partes else ""


# ------------------------------------------------------------------ modelos (cadena de APIs + Ollama, ver modelos.py)
def llamar(uso, mensajes, max_tokens=3000, timeout=60):
    return modelos.llamar(uso, mensajes, max_tokens, timeout)


def normal(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\[.*?\]|\(.*?\)", " ", t)).split()


VACIAS = set("el la los las un una de del que en y a por para con se lo le su es esta este esto aqui ahi "
             "nosotros ustedes no si o al como mas ya hay".split())


def parecidas(a, b):
    """¿Es la misma pregunta dicha con otras palabras? (la de la diapositiva vuelve a salir cada vez)"""
    na, nb = normal(a), normal(b)
    if not na or not nb:
        return False
    if difflib.SequenceMatcher(None, " ".join(na), " ".join(nb)).ratio() > 0.62:
        return True
    ca, cb = set(na) - VACIAS, set(nb) - VACIAS
    return bool(ca and cb) and len(ca & cb) / min(len(ca), len(cb)) >= 0.7


def json_de(texto):
    m = re.search(r"\{.*\}", texto or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def avisar(titulo, texto, urgente=False, reemplaza=None):
    """Globito del escritorio. Devuelve su id para reemplazarlo luego (borrador → respuesta final)."""
    if not leer_ajustes().get("globitos", True) or not shutil.which("notify-send"):
        return None       # sin libnotify la app usa las notificaciones del navegador
    cmd = ["notify-send", "-p", "-a", "Tomo", "-i", "audio-headset", "-u", "critical" if urgente else "normal",
           "-t", "90000" if urgente else "12000"]
    if reemplaza:
        cmd += ["-r", str(reemplaza)]
    r = subprocess.run(cmd + [titulo, texto], capture_output=True, text=True, check=False)
    return r.stdout.strip() or None


def punto_callado(buf, ventana):
    """Índice del tramo de 200 ms más callado dentro de los últimos `ventana` samples."""
    zona = buf[-ventana:]
    paso = TASA // 5
    energias = [float(np.mean(zona[i:i + paso] ** 2)) for i in range(0, max(1, len(zona) - paso), paso // 2)]
    i = int(np.argmin(energias)) * (paso // 2) + paso // 2
    return len(buf) - len(zona) + i


def captura(destino: Path):
    """Captura reducida (JPEG) de la pantalla de la reunión. Devuelve (base64, huella 32×18 gris) o (None, None)."""
    png = destino.parent / f".captura-{threading.get_ident()}.png"
    try:
        pantalla.capturar(png, leer_ajustes().get("monitor") or "")
        destino.write_bytes(pantalla.jpeg(png, 1600, 75))
        pantalla._vivo.update(t=time.time(), jpg=pantalla.jpeg(png, 1100), monitor=leer_ajustes().get("monitor") or "")
        h = np.frombuffer(pantalla.huella(png), np.uint8).astype(np.int16)
        return base64.b64encode(destino.read_bytes()).decode(), h
    except Exception as e:
        log("captura falló:", e)
        return None, None
    finally:
        png.unlink(missing_ok=True)


# ------------------------------------------------------------------ prompts
DETECTAR = """Eres Tomo, el copiloto del usuario en una reunión o curso en línea. El usuario es PARTICIPANTE, no el expositor.
Recibes lo último que se dijo (transcripción automática, con errores) y quizá una captura de la pantalla.
Decide si en lo ÚLTIMO dicho (o en la diapositiva compartida) hay una pregunta hecha al público o al usuario.
Las preguntas retóricas que el expositor responde él mismo no cuentan; tampoco las de logística sin importancia
si nadie espera respuesta. Tampoco cuentan muletillas de control («¿vale?», «¿sí?», «¿alguien más?», «ok?»).
Si la pregunta ya está en YA DETECTADAS (aunque con otras palabras, o porque sigue en la diapositiva), responde
"pregunta": false: solo interesan preguntas NUEVAS. Responde SOLO un JSON: {"pregunta": true|false,
"cual": "la pregunta, corta y corregida", "pantalla": "texto de la diapositiva que sirve para responder, o vacío",
"borrador": "si es pregunta: lo que el usuario podría decir, 1-2 frases en primera persona"}."""

AYUDAME = """Eres Tomo, el copiloto del usuario en una reunión o curso en línea. El usuario es PARTICIPANTE y acaba
de pulsar «Ayúdame»: necesita saber qué decir AHORA. Mira lo último que se dijo y la pantalla, identifica la
pregunta más reciente que le hacen (a él o al público) o, si no hay, el punto del que se habla. Responde SOLO un JSON:
{"pregunta": true, "cual": "la pregunta o el tema, corta", "pantalla": "texto útil de la diapositiva, o vacío",
"borrador": "lo que el usuario podría decir, 1-2 frases en primera persona"}."""

RESPONDER = """Eres Tomo, el copiloto del usuario, PARTICIPANTE de una reunión o curso en línea, a quien le hacen
una pregunta. Escribe lo que podría decir o poner en el chat: 2-3 frases claras y correctas, en primera persona,
en el idioma de la reunión, sin rodeos ni saludos. Si la transcripción tiene errores evidentes en siglas o nombres
(MCP, RAG, LLM…), interprétalos según el tema. Responde SOLO el texto de la respuesta."""

RESOLVER = """Eres Tomo, el copiloto del usuario, PARTICIPANTE de una reunión o curso en línea, a quien le pusieron
un ejercicio. Recibes el enunciado (texto y/o imagen), las reglas o partes de la clase que el usuario marcó como
importantes, y lo último que se dijo. Resuelve el ejercicio cumpliendo AL PIE DE LA LETRA el enunciado y lo que
enseñó el expositor (límites de líneas, formato, campos, estilo). Responde en Markdown, en el idioma de la reunión:
1) Primero la solución lista para copiar y pegar, dentro de un bloque ``` ```.
2) Luego «Por qué cumple:» con 2-4 viñetas cortas que conecten la solución con las reglas de la clase.
Sin saludos ni rodeos. Si el usuario pide un cambio sobre una solución anterior, entrega la versión nueva completa."""

PANTALLA = """Describe la pantalla compartida de un curso o reunión (ignora la interfaz de la app de videollamada y
del escritorio). Responde SOLO un JSON: {"util": true|false, "titulo": "título de la diapositiva o qué se muestra",
"resumen": "lo importante que se lee, 1-3 frases", "herramientas": ["herramientas, sitios, repos o productos visibles"]}.
"util": false si no se comparte nada con contenido (solo caras, escritorio vacío, pantalla de espera)."""

NOTAS = """Eres Tomo y tomas las notas del usuario de un curso o reunión en línea. Recibes la transcripción
automática (con errores de reconocimiento), las diapositivas vistas y las preguntas que surgieron. Escribe
Markdown, en el idioma de la reunión, con EXACTAMENTE estas secciones:
## Resumen (3-6 líneas)
## Temas tratados (viñetas)
## Herramientas, sitios y repos mencionados (viñetas: **nombre** — qué es / para qué lo mencionaron; enlace oficial solo si lo conoces con certeza)
## Ideas para mis proyectos (máx. 4 viñetas, solo ideas realistas que salgan DIRECTO de lo visto, nombrando el proyecto del usuario de la lista; si no hay lista o ninguna idea clara, escribe «Ninguna clara»)
## Preguntas y respuestas (las que surgieron, con la respuesta sugerida o la que se dio)
## Para profundizar (2-5 cosas concretas para investigar o probar)
No inventes nada que no esté en el material; si algo es dudoso márcalo con (?). Ignora la charla sin contenido.
Las capturas pueden mostrar otras apps del usuario ajenas a la reunión (editor, terminal…): ignóralas."""


# ------------------------------------------------------------------ la sesión
class Sesion:
    def __init__(self, tema, ajustes, previa=None):
        self.inicio = time.time()
        self.id = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        if previa:      # el motor se reinició en plena reunión: seguir la misma
            self.inicio = datetime.fromisoformat(previa["inicio"]).timestamp()
            self.id = previa["id"]
        self.carpeta = RAIZ / f"reunion-{self.id}"
        (self.carpeta / "pantallas").mkdir(parents=True, exist_ok=True)
        self.tema = tema.strip()
        self.ajustes = ajustes
        self.estado = "cargando"      # cargando · escuchando · pausado · resumiendo · listo · error
        self.error = ""
        self.detalle = ""             # qué está haciendo mientras carga (p. ej. descargando el modelo de voz)
        self.lineas = []              # {t, quien, texto}
        self.preguntas = []           # {id, t, cual, pantalla, respuesta, estado, variantes}
        self.pantallas = []           # {t, titulo, resumen, herramientas, imagen}
        self.ejercicios = []          # {id, t, titulo, mensajes:[{rol, texto, imagenes, citas}], estado}
        self.nota = ""
        self.ideas = []
        self.fin = None
        self.version = 0
        self.lock = threading.RLock()
        self.parar = threading.Event()
        self.colas = {"sistema": deque()}
        if ajustes.get("microfono"):
            self.colas["mic"] = deque()
        self.procesos = []
        self.oido = None
        self.fuente = ""
        self.apps_oidas = 0
        self.ultima_auto = 0.0
        self.huella = None
        self.prompt = f"Reunión o curso. {self.tema}".strip()
        if previa:
            self.lineas = previa.get("lineas") or []
            # en sesion.json preguntas y ejercicios van del más nuevo al más viejo: volver al orden de llegada
            self.preguntas = sorted(previa.get("preguntas") or [], key=lambda q: q.get("s", 0))
            self.pantallas = previa.get("pantallas") or []
            self.ejercicios = sorted(previa.get("ejercicios") or [], key=lambda e: e.get("id", ""))
            for x in self.preguntas + self.ejercicios:
                if x.get("estado") == "pensando":
                    x["estado"] = "lista" if x.get("respuesta") else "error"
            return
        (self.carpeta / "transcripcion.md").write_text(
            f"# {self.tema or 'Reunión'} — {datetime.now():%Y-%m-%d %H:%M}\n\n"
            "Transcripción automática (Whisper). «Ellos» = lo que suena en tus audífonos; «Yo» = tu micrófono.\n\n",
            encoding="utf-8")

    # ---- estado público
    def resumen(self, completo=False):
        with self.lock:
            fin = self.fin or time.time()
            seg = fin - self.inicio
            paso = 15 if seg < 20 * 60 else 60          # barras de 15 s al principio, de 1 min después
            ritmo = [0] * (int(seg // paso) + 1)
            for l in self.lineas:
                ritmo[min(len(ritmo) - 1, int(l["s"] // paso))] += len(l["texto"].split())
            herramientas = list(dict.fromkeys(h.strip() for p in self.pantallas for h in p["herramientas"] if h.strip()))
            d = {"id": self.id, "tema": self.tema, "estado": self.estado, "error": self.error, "detalle": self.detalle,
                 "inicio": datetime.fromtimestamp(self.inicio).isoformat(timespec="seconds"),
                 "fin": datetime.fromtimestamp(self.fin).isoformat(timespec="seconds") if self.fin else "",
                 "seg": int(seg), "ritmo": ritmo, "ritmo_paso": paso, "herramientas": herramientas[:40],
                 "marcas_p": [p["s"] for p in self.pantallas], "n_pantallas": len(self.pantallas),
                 "ideas": self.ideas, "minutos": int(seg // 60), "carpeta": str(self.carpeta),
                 "n_lineas": len(self.lineas), "nota": self.nota, "version": self.version,
                 "microfono": "mic" in self.colas, "fuente": self.fuente, "apps_oidas": self.apps_oidas,
                 "modo_oido": getattr(self, "modo_oido", ""),
                 "lineas": self.lineas if completo else self.lineas[-80:],
                 "preguntas": list(reversed(self.preguntas)),
                 "pantalla": self.pantallas[-1] if self.pantallas else None,
                 "ejercicios": [publico(e) for e in reversed(self.ejercicios)]}
            if completo:
                d["pantallas"] = self.pantallas
                d["lineas"] = self.lineas
            return d

    def guardar(self):
        with self.lock:
            self.version += 1
            datos = self.resumen(completo=True)
        tmp = self.carpeta / ".sesion.json.tmp"
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.carpeta / "sesion.json")

    # ---- arranque
    def empezar(self):
        self.hilo = threading.Thread(target=self._correr, daemon=True)
        self.hilo.start()

    def _correr(self):
        try:
            self.detalle = ("Cargando el modelo de voz. La primera vez se descarga (entre 0,5 y 1,6 GB) "
                            "y puede tardar unos minutos.")
            try:
                cargar_whisper(self.ajustes.get("modelo", "auto"))
            except Exception as e:
                raise RuntimeError(f"no pude cargar el modelo de voz (¿sin internet la primera vez?): {e}") from e
            self.detalle = ""
            self._abrir_audio()
            with self.lock:
                self.estado = "escuchando"
            self.guardar()
            log("escuchando", self.carpeta)
            threading.Thread(target=self._mirar_pantalla, daemon=True).start()
            while True:      # el oído nunca se detiene por un error: se anota y sigue escuchando
                try:
                    self._transcribir()
                    break
                except Exception as e:
                    traceback.print_exc()
                    log("el oído tuvo un error y sigue escuchando:", e)
                    if self.parar.is_set():
                        break
                    time.sleep(1)
        except Exception as e:
            traceback.print_exc()
            with self.lock:
                self.estado, self.error = "error", str(e)
            self.guardar()

    def _abrir_audio(self):
        prueba = os.environ.get("TOMO_PRUEBA_WAV")   # para probar sin reunión: un .wav de 16 kHz mono
        if prueba:
            import wave
            w = wave.open(prueba)
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            if w.getframerate() != TASA:
                audio = np.interp(np.arange(0, len(audio), w.getframerate() / TASA), np.arange(len(audio)), audio).astype(np.float32)

            def alimentar():
                for i in range(0, len(audio), TASA // 10):
                    while self.estado == "pausado":
                        time.sleep(0.1)
                    self.colas["sistema"].append(audio[i:i + TASA // 10])
                    time.sleep(0.1)
            threading.Thread(target=alimentar, daemon=True).start()
            return
        # «oído» propio: un sink virtual al que se enlazan los streams de la app de reunión (Zoom, Teams…).
        # Así se graba el audio antes del volumen (funciona con audífonos y el volumen bajo).
        # Ajuste «salida» (Meet/Teams en el navegador…) → lo que suena por la salida por defecto.
        # Sin PipeWire (p. ej. Ubuntu 22.04 con PulseAudio) se graba la salida con parec.
        motor = motor_audio()
        if motor == "nada":
            raise RuntimeError("no encuentro el audio del sistema: Tomo necesita PipeWire (pw-record) o PulseAudio (parec)")
        pw = ["pw-record"]
        # --raw existe desde PipeWire 1.2; en 1.0 (Ubuntu 24.04) no está y el stdout ya sale sin cabecera
        fmt = (["--raw"] if pw_tiene_raw() else []) + ["--rate", str(TASA), "--channels", "1", "--format", "s16", "-"]
        fuentes = {}
        if motor == "pipewire" and self.ajustes.get("fuente", "app") in ("app", "zoom"):
            try:
                self.oido = Oido()
                self.fuente = "app directa"
                # ojo: «<sink>.monitor» no existe como nodo y pw-record caería al micrófono
                fuentes["sistema"] = pw + ["--target", Oido.NOMBRE, "-P", "{ stream.capture.sink=true }"] + fmt
                threading.Thread(target=self._vigilar_oido, daemon=True).start()
            except Exception as e:
                log("no pude crear el oído propio; escucho la salida de audio:", e)
                self.oido = None
        if "sistema" not in fuentes:
            self.fuente = "salida de audio"
            if motor == "pipewire":
                fuentes["sistema"] = pw + ["-P", "{ stream.capture.sink=true }"] + fmt
            else:
                fuentes["sistema"] = ["parec", f"--device={monitor_pulse()}", "--rate", str(TASA), "--channels", "1",
                                      "--format", "s16le", "--raw"]
        if "mic" in self.colas:          # micrófono por defecto (easyeffects con RNNoise si está)
            fuentes["mic"] = pw + fmt if motor == "pipewire" else ["parec", "--rate", str(TASA), "--channels", "1",
                                                                  "--format", "s16le", "--raw"]
        self.motor_audio = motor
        self.fuentes = fuentes
        self.grabadores = {}
        for pista in fuentes:
            self._grabar(pista)
        threading.Thread(target=self._vigilar_audio, daemon=True).start()

    def _grabar(self, pista):
        p = subprocess.Popen(self.fuentes[pista], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.ultimas = deque(maxlen=3)      # lo último que dijo por stderr (se drena para que nunca se llene)
        threading.Thread(target=lambda: [p.ultimas.append(l.decode(errors="replace").strip()) for l in p.stderr], daemon=True).start()
        self.procesos.append(p)
        self.grabadores[pista] = p
        threading.Thread(target=self._leer, args=(pista, p), daemon=True).start()

    def _vigilar_audio(self):
        """Si pw-record se cae (cambio de audífonos, PipeWire reiniciado…), se relanza: el oído no se apaga."""
        while not self.parar.wait(3):
            for pista, p in list(self.grabadores.items()):
                if p.poll() is not None:
                    time.sleep(0.2)
                    motivo = " ".join(getattr(p, "ultimas", [])) or f"código {p.returncode}"
                    log(f"la grabación «{pista}» se cortó ({motivo[-200:]}); la relanzo")
                    if self.oido and pista == "sistema":
                        try:
                            self.oido.cerrar()
                            self.oido = Oido()
                        except Exception as e:
                            log("oído:", e)
                    self._grabar(pista)

    def _vigilar_oido(self):
        while not self.parar.is_set():
            try:
                self.apps_oidas = self.oido.enlazar()
                self.modo_oido = self.oido.modo
            except Exception as e:
                log("oído:", e)
            self.parar.wait(1)

    def _leer(self, pista, proc):
        bloque = TASA // 10 * 2      # 100 ms
        while not self.parar.is_set():
            datos = proc.stdout.read(bloque)
            if not datos:
                break
            if self.estado == "pausado":
                continue
            self.colas[pista].append(np.frombuffer(datos, np.int16).astype(np.float32) / 32768)

    # ---- oídos: cortar por pausas reales (Silero) y transcribir
    def _transcribir(self):
        resto = {p: np.zeros(0, np.float32) for p in self.colas}
        while True:
            terminando = self.parar.is_set()
            for pista, cola in self.colas.items():
                nuevos = []
                while cola:
                    nuevos.append(cola.popleft())
                buf = np.concatenate([resto[pista], *nuevos]) if nuevos else resto[pista]
                resto[pista] = buf
                if buf.size < TASA:
                    continue
                voz = get_speech_timestamps(buf, VAD)
                if not voz:
                    if buf.size > 3 * TASA:
                        resto[pista] = buf[-TASA // 2:]     # solo silencio: se queda la última media s
                    continue
                cola_silencio = buf.size - voz[-1]["end"]
                largo = buf.size > MAX_TROZO * TASA
                if not (terminando or largo or cola_silencio > 0.45 * TASA):
                    continue                                 # la frase sigue: esperar
                if terminando:
                    corte = buf.size
                elif cola_silencio > 0.45 * TASA:
                    corte = voz[-1]["end"]
                elif len(voz) > 1 and voz[-2]["end"] > 5 * TASA:
                    corte = voz[-2]["end"]                   # 15 s seguidos: cortar en la última pausa
                else:
                    corte = punto_callado(buf, 5 * TASA)     # sin pausas: donde menos suena, no a media palabra
                desde = max(0, voz[0]["start"] - TASA // 5)
                resto[pista] = buf[corte:]
                self._oir(pista, buf[desde:corte])
            if terminando:
                return
            time.sleep(0.4)

    def _oir(self, pista, audio):
        """Transcribe un trozo y, si suena a pregunta, la analiza 2,5 s después (que termine la frase)."""
        if audio.size < TASA // 2:
            return
        try:
            with _whisper_lock:
                idioma = leer_ajustes().get("idioma") or "es"
                segs, _ = WHISPER["modelo"].transcribe(audio, language=None if idioma == "auto" else idioma, beam_size=5, vad_filter=False,
                                                       condition_on_previous_text=False, initial_prompt=self.prompt)
                texto = " ".join(limpiar(s.text) for s in segs if fiable(s, self.prompt)).strip()
            self.fallos_whisper = 0
        except RuntimeError as e:      # p. ej. «CUDA out of memory» porque otra app pidió la tarjeta: no tumbar la reunión
            self.fallos_whisper = getattr(self, "fallos_whisper", 0) + 1
            log(f"whisper falló ({self.fallos_whisper}): {e}")
            gc.collect()
            if self.fallos_whisper >= 3:
                log("recargo whisper en CPU para seguir escuchando")
                with _whisper_lock:
                    from faster_whisper import WhisperModel
                    WHISPER["modelo"] = None
                    gc.collect()
                    cpu = wh.modelo_cpu()
                    WHISPER["modelo"] = WhisperModel(cpu, device="cpu", compute_type="int8",
                                                     cpu_threads=max(2, (os.cpu_count() or 4) - 2))
                    WHISPER["nombre"], WHISPER["donde"] = f"{cpu}-cpu", "cpu"
                self.fallos_whisper = 0
            return
        if not texto:
            return
        quien = "Yo" if pista == "mic" else "Ellos"
        ahora = datetime.now()
        with self.lock:
            if self.lineas and self.lineas[-1]["texto"] == texto:
                return
            self.lineas.append({"t": f"{ahora:%H:%M:%S}", "s": round(time.time() - self.inicio, 1),
                                "quien": quien, "texto": texto})
        with (self.carpeta / "transcripcion.md").open("a", encoding="utf-8") as f:
            f.write(f"- **{ahora:%H:%M:%S} · {quien}:** {texto}\n")
        self.guardar()
        if pista == "sistema" and PREGUNTA.search(texto) and time.time() - self.ultima_auto > 12:
            self.ultima_auto = time.time()
            threading.Timer(2.5, self.ayudar).start()

    # ---- ojos: la pantalla compartida cada minuto (solo si cambió)
    def _mirar_pantalla(self):
        while not self.parar.wait(20):
            if self.estado != "escuchando" or not leer_ajustes().get("pantalla", True):
                continue
            try:
                self.mirar_pantalla()
            except Exception as e:
                log("pantalla:", e)

    def mirar_pantalla(self, forzar=False):
        ruta = self.carpeta / "pantallas" / f"{datetime.now():%H%M%S}.jpg"
        img, huella = captura(ruta)
        if img is None:
            return None
        if not forzar and self.huella is not None and np.abs(huella - self.huella).mean() < 3:
            ruta.unlink(missing_ok=True)
            return None
        self.huella = huella
        try:
            d = json_de(llamar("vision", [{"role": "system", "content": PANTALLA}, {"role": "user", "content": [
                {"type": "text", "text": f"Tema: {self.tema or 'no indicado'}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}]))
        except Exception as e:
            log("nadie pudo leer la pantalla; guardo la captura sin leer:", e)
            d = {"util": True, "titulo": "Captura sin leer", "resumen": "Ningún modelo con visión respondió; la captura quedó guardada.",
                 "herramientas": [], "sin_leer": True}
        if not d.get("util"):
            ruta.unlink(missing_ok=True)
            return None
        p = {"t": datetime.now().strftime("%H:%M:%S"), "s": round(time.time() - self.inicio, 1), "titulo": d.get("titulo", ""), "resumen": d.get("resumen", ""),
             "herramientas": d.get("herramientas") or [], "imagen": ruta.name}
        with self.lock:
            self.pantallas.append(p)
        self.guardar()
        log("pantalla:", p["titulo"])
        return p

    # ---- preguntas
    def contexto(self, n=20):
        with self.lock:
            return "\n".join(f"{l['quien']}: {l['texto']}" for l in self.lineas[-n:])

    def ayudar(self, forzado=False):
        """Auto (oyó algo con forma de pregunta) o forzado («Ayúdame ahora»)."""
        try:
            self._ayudar(forzado)
        except Exception as e:
            log("no pude analizar la pregunta:", e)
            if forzado:
                with self.lock:
                    self.preguntas.append({"id": f"q{int(time.time() * 1000)}", "t": datetime.now().strftime("%H:%M:%S"),
                                           "s": round(time.time() - self.inicio, 1), "cual": "No pude pensar una respuesta",
                                           "pantalla": "", "respuesta": f"Ningún modelo de IA respondió ({e}). Revisa Ajustes › Modelos de IA.",
                                           "estado": "error", "pedida": True, "imagen": "", "contexto": ""})
                self.guardar()

    def _ayudar(self, forzado=False):
        t0 = time.time()
        ultimo = self.contexto(3 if not forzado else 6)
        texto = (f"{perfil()}\nTema de la reunión: {self.tema or 'no indicado'}\n\nContexto reciente:\n{self.contexto(25)}"
                 f"\n\nLO ÚLTIMO DICHO:\n{ultimo or '(nada aún)'}")
        with self.lock:
            ya = [q["cual"] for q in self.preguntas[-8:]]
        if ya and not forzado:
            texto += "\n\nYA DETECTADAS (no repetir):\n" + "\n".join(f"- {c}" for c in ya)
        if self.pantallas:
            texto += f"\n\nÚltima diapositiva vista: {self.pantallas[-1]['titulo']} — {self.pantallas[-1]['resumen']}"
        ruta = self.carpeta / "pantallas" / f"{datetime.now():%H%M%S}-pregunta.jpg"
        img, _ = captura(ruta) if leer_ajustes().get("pantalla", True) or forzado else (None, None)
        contenido = [{"type": "text", "text": texto}]
        if img:
            contenido.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})
        sistema = AYUDAME if forzado else DETECTAR
        try:
            d = json_de(llamar("vision", [{"role": "system", "content": sistema}, {"role": "user", "content": contenido}]))
        except Exception as e:
            log("gemini falló, uso rapido:", e)
            d = json_de(llamar("texto", [{"role": "system", "content": sistema}, {"role": "user", "content": texto}], 600))
        if not d.get("pregunta") and not forzado:
            ruta.unlink(missing_ok=True)
            log(f"no era pregunta ({time.time() - t0:.1f}s)")
            return
        if not forzado:
            cual = d.get("cual") or ""
            with self.lock:
                repetida = next((x for x in reversed(self.preguntas) if time.time() - self.inicio - x["s"] < 900
                                 and parecidas(cual, x["cual"])), None)
            if repetida:
                ruta.unlink(missing_ok=True)
                log(f"repetida, no se vuelve a responder: {cual}")
                return
        q = {"id": f"q{int(time.time() * 1000)}", "t": datetime.now().strftime("%H:%M:%S"),
             "s": round(time.time() - self.inicio, 1),
             "cual": d.get("cual") or ultimo[-160:], "pantalla": d.get("pantalla", ""),
             "respuesta": (d.get("borrador") or "").strip(), "borrador": bool(d.get("borrador")),
             "estado": "pensando", "pedida": forzado, "imagen": ruta.name if img else "", "contexto": texto}
        with self.lock:
            self.preguntas.append(q)
        self.guardar()
        q["aviso"] = avisar(f"❓ {q['cual'][:120]}", (q["respuesta"] + "\n\n(borrador, mejorando…)") if q["respuesta"]
                            else "pensando qué responder…", urgente=bool(q["respuesta"]))
        self.responder(q)
        log(f"pregunta en {time.time() - t0:.1f}s: {q['cual']}")

    def responder(self, q, variante=None):
        pedido = f"{q['contexto']}\n\nPREGUNTA: {q['cual']}\n\nEN LA PANTALLA: {q['pantalla'] or '(nada útil)'}"
        if variante == "otra":
            pedido += f"\n\nYa se sugirió esto; da una respuesta DISTINTA (otro enfoque):\n{q['respuesta']}"
        elif variante == "corta":
            pedido += f"\n\nHaz MÁS CORTA esta respuesta (una sola frase, natural para decir en voz alta):\n{q['respuesta']}"
        with self.lock:
            q["estado"] = "pensando"
        self.guardar()
        try:
            try:
                r = llamar("texto", [{"role": "system", "content": RESPONDER}, {"role": "user", "content": pedido}])
            except Exception as e:
                log("codigo falló, uso gemini:", e)
                r = llamar("vision", [{"role": "system", "content": RESPONDER}, {"role": "user", "content": pedido}])
            with self.lock:
                if q["respuesta"] and not q.get("borrador"):
                    q.setdefault("anteriores", []).append(q["respuesta"])
                q["respuesta"], q["estado"], q["borrador"] = r, "lista", False
            with (self.carpeta / "sugerencias.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## {q['t']} — {q['cual']}{' (' + variante + ')' if variante else ''}\n\n{r}\n")
            q["aviso"] = avisar(f"💡 {q['cual'][:120]}", r, urgente=True, reemplaza=q.get("aviso"))
        except Exception as e:
            with self.lock:
                q["estado"], q["respuesta"] = "error", q["respuesta"] or f"No se pudo: {e}"
        self.guardar()

    def lineas_desde(self, n):
        with self.lock:
            return self.lineas[n:]

    def pregunta(self, qid):
        with self.lock:
            return next((q for q in self.preguntas if q["id"] == qid), None)

    # ---- terminar: soltar audio y GPU, escribir la nota final
    def terminar(self):
        if self.parar.is_set():
            return
        self.parar.set()
        for p in self.procesos:
            p.terminate()
        if self.oido:
            self.oido.cerrar()
        with self.lock:
            self.estado, self.fin = "resumiendo", time.time()
        self.guardar()
        threading.Thread(target=self._cerrar, daemon=True).start()

    def _cerrar(self):
        self.hilo.join(timeout=60)      # que el transcriptor pase lo último
        soltar_whisper()
        try:
            self.nota = escribir_nota(self) if self.lineas else ""      # sin IA también queda una nota
            if self.nota:
                m = re.search(r"## Ideas para mis proyectos\s*\n(.*?)(\n## |\Z)", Path(self.nota).read_text(encoding="utf-8"), re.S)
                self.ideas = [re.sub(r"^[-*]\s*|\*\*", "", x).strip() for x in (m.group(1) if m else "").splitlines()
                              if x.strip().startswith(("-", "*")) and "ninguna clara" not in x.lower()][:6]
            with self.lock:
                self.estado = "listo"
            avisar("📚 Tomo: reunión guardada", f"Nota: {Path(self.nota).name}" if self.nota
                   else "No hubo nada que transcribir.")
        except Exception as e:
            traceback.print_exc()
            with self.lock:
                self.estado, self.error = "error", f"no se pudo escribir la nota: {e}"
        self.guardar()


# ------------------------------------------------------------------ ejercicios: subir enunciado (texto/imagen) y resolver
LIBRE = {"carpeta": RAIZ / "ejercicios", "ejercicios": [], "lock": threading.RLock()}


def donde_ejercicios():
    s = SESION["actual"]
    if s and s.estado in ("cargando", "escuchando", "pausado"):
        return s, s.ejercicios, s.lock, s.carpeta / "pantallas"
    return None, LIBRE["ejercicios"], LIBRE["lock"], LIBRE["carpeta"]


def guardar_imagen(dataurl, carpeta: Path):
    m = re.match(r"data:image/(png|jpeg|jpg|webp);base64,(.+)", dataurl or "", re.S)
    if not m:
        return None
    carpeta.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    nombre = f"ej-{datetime.now():%H%M%S}-{int(time.time() * 1000) % 1000:03d}.{ext}"
    (carpeta / nombre).write_bytes(base64.b64decode(m.group(2)))
    return nombre


def resolver(d):
    """Crea un ejercicio (o sigue uno con `sigue`) y lo resuelve en un hilo. Devuelve su id."""
    s, lista, lock, carpeta = donde_ejercicios()
    imgs = [n for n in (guardar_imagen(x, carpeta) for x in (d.get("imagenes") or [])[:4]) if n]
    msj = {"rol": "usuario", "texto": str(d.get("texto", "")).strip()[:20000], "imagenes": imgs,
           "citas": [str(c)[:2000] for c in (d.get("citas") or [])][:60], "datos": [x for x in (d.get("imagenes") or [])[:4]]}
    with lock:
        e = next((x for x in lista if x["id"] == d.get("sigue")), None)
        if e is None:
            e = {"id": f"e{int(time.time() * 1000)}", "t": datetime.now().strftime("%H:%M:%S"),
                 "titulo": (msj["texto"] or "Ejercicio de la imagen")[:90], "mensajes": [], "estado": "pensando",
                 "sesion": s.id if s else ""}
            lista.append(e)
        e["mensajes"].append(msj)
        e["estado"] = "pensando"
    if s:
        s.guardar()
    threading.Thread(target=_resolver, args=(s, e, lock), daemon=True).start()
    return e["id"]


def _resolver(s, e, lock):
    ctx = ""
    if s:
        ctx = f"{perfil()}\nTema de la clase: {s.tema or 'no indicado'}\n\nLo último que se dijo:\n{s.contexto(45)}"
        if s.pantallas:
            ctx += f"\n\nÚltima diapositiva: {s.pantallas[-1]['titulo']} — {s.pantallas[-1]['resumen']}"
    mensajes = [{"role": "system", "content": RESOLVER}]
    hay_img = False
    for i, m in enumerate(e["mensajes"]):
        if m["rol"] == "copiloto":
            mensajes.append({"role": "assistant", "content": m["texto"]})
            continue
        txt = ""
        if i == 0 and ctx:
            txt += ctx + "\n\n"
        if m.get("citas"):
            txt += "LO QUE EL USUARIO MARCÓ DE LA CLASE (reglas/indicaciones):\n" + "\n".join(f"- {c}" for c in m["citas"]) + "\n\n"
        txt += ("ENUNCIADO / PEDIDO:\n" if i == 0 else "AHORA EL USUARIO PIDE:\n") + (m["texto"] or "(el enunciado está en la imagen)")
        partes = [{"type": "text", "text": txt}]
        for dato in m.get("datos") or []:
            hay_img = True
            partes.append({"type": "image_url", "image_url": {"url": dato}})
        mensajes.append({"role": "user", "content": partes if len(partes) > 1 else txt})
    try:
        try:
            r = llamar("vision" if hay_img else "texto", mensajes, max_tokens=4000, timeout=120)
        except Exception as ex:
            if hay_img:
                raise
            log("el modelo de texto falló en el ejercicio, pruebo el de visión:", ex)
            r = llamar("vision", mensajes, max_tokens=4000, timeout=120)
        with lock:
            e["mensajes"].append({"rol": "copiloto", "texto": r, "t": datetime.now().strftime("%H:%M:%S")})
            e["estado"] = "lista"
        if s:
            with (s.carpeta / "sugerencias.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## {e['t']} — Ejercicio: {e['titulo']}\n\n{r}\n")
        avisar("✍️ Ejercicio resuelto", r[:300])
    except Exception as ex:
        with lock:
            e["mensajes"].append({"rol": "copiloto", "texto": f"No se pudo resolver: {ex}", "error": True})
            e["estado"] = "error"
    if s:
        s.guardar()


def publico(e):
    """Ejercicio sin las imágenes en base64 (pesan)."""
    return {**e, "mensajes": [{k: v for k, v in m.items() if k != "datos"} for m in e["mensajes"]]}


# ------------------------------------------------------------------ oído: audio de Zoom antes del volumen


class Oido:
    """Sink virtual «tomo_oido»: se le enlazan (además de la salida real) los streams de las apps de
    reunión, y se graba su monitor. No depende del volumen ni del dispositivo (audífonos BT, parlantes…)."""
    NOMBRE = "tomo_oido"

    def __init__(self):
        if not shutil.which("pactl"):
            raise RuntimeError("falta pactl (pulseaudio-utils)")
        Oido.limpiar()
        r = subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={self.NOMBRE}",
                            "sink_properties=device.description=Tomo-oido node.virtual=true",
                            "channels=2", "rate=48000"], capture_output=True, text=True, timeout=10)
        self.modulo = r.stdout.strip()
        if r.returncode or not self.modulo.isdigit():
            raise RuntimeError(f"pactl no creó el oído: {(r.stderr or r.stdout).strip()}")
        self.modo = "nada"

    @classmethod
    def limpiar(cls):
        """Quita oídos que quedaron de una vez que Tomo se cerró de golpe (si no, se apilan «tomo_oido» repetidos)."""
        try:
            out = subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True, timeout=10).stdout
            for l in out.splitlines():
                if f"sink_name={cls.NOMBRE}" in l:
                    subprocess.run(["pactl", "unload-module", l.split()[0]], capture_output=True, timeout=10)
        except Exception:
            pass

    def enlazar(self):
        objs = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10).stdout or "[]")
        props = {o["id"]: (o.get("info") or {}).get("props") or {} for o in objs}
        nodo_oido = next((i for i, p in props.items() if p.get("node.name") == self.NOMBRE
                          and p.get("media.class") == "Audio/Sink"), None)
        if nodo_oido is None:
            return 0
        salidas = {i: p for i, p in props.items() if p.get("media.class") == "Stream/Output/Audio"}
        nombre = lambda p: " ".join(str(p.get(k, "")) for k in ("application.name", "application.process.binary", "node.name"))
        apps = {i for i, p in salidas.items() if re.search(leer_ajustes().get("apps") or "zoom", nombre(p), re.I)}
        self.modo = "app"
        if not apps:
            # sin Zoom/Teams/Webex: la reunión está en el navegador (Meet, Teams web…) → oír todo lo que suena,
            # menos sonidos del sistema y lo propio
            apps = {i for i, p in salidas.items() if p.get("media.role") not in ("event", "Notification", "notification")
                    and not re.search(r"tomo|speech-dispatcher|notify|canberra", nombre(p), re.I)}
            self.modo = "todo" if apps else "nada"
        puertos = [(o["id"], props[o["id"]]) for o in objs if o.get("type") == "PipeWire:Interface:Port"]
        entrada = {p.get("audio.channel"): i for i, p in puertos
                   if int(p.get("node.id", -1)) == nodo_oido and p.get("port.direction") == "in"}
        hechos = {((o.get("info") or {}).get("output-port-id"), (o.get("info") or {}).get("input-port-id"))
                  for o in objs if o.get("type") == "PipeWire:Interface:Link"}
        for i, p in puertos:
            if int(p.get("node.id", -1)) not in apps or p.get("port.direction") != "out":
                continue
            canal = p.get("audio.channel", "MONO")
            destinos = [entrada.get(canal)] if canal in entrada else list(entrada.values())   # MONO → FL y FR
            for d in destinos:
                if d is not None and (i, d) not in hechos:
                    subprocess.run(["pw-link", str(i), str(d)], capture_output=True)
        return len(apps)

    def cerrar(self):
        if self.modulo:
            subprocess.run(["pactl", "unload-module", self.modulo], capture_output=True)
            self.modulo = ""


def motor_audio():
    """pipewire · pulse (PulseAudio sin PipeWire, p. ej. Ubuntu 22.04) · nada."""
    try:
        if shutil.which("pw-record") and subprocess.run(["pw-cli", "info", "0"], capture_output=True, timeout=4).returncode == 0:
            return "pipewire"
    except Exception:
        pass
    try:
        if shutil.which("parec") and subprocess.run(["pactl", "info"], capture_output=True, timeout=4).returncode == 0:
            return "pulse"
    except Exception:
        pass
    return "nada"


_raw = {}


def pw_tiene_raw():
    if "v" not in _raw:
        try:
            _raw["v"] = "--raw" in subprocess.run(["pw-record", "--help"], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            _raw["v"] = False
    return _raw["v"]


def monitor_pulse():
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=4).stdout.strip()
        if sink:
            return f"{sink}.monitor"
    except Exception:
        pass
    return "@DEFAULT_MONITOR@"


# ------------------------------------------------------------------ Whisper (uno solo, se suelta al terminar)
WHISPER = {"modelo": None, "nombre": "", "donde": ""}
_whisper_lock = threading.Lock()


def cargar_whisper(nombre):
    with _whisper_lock:
        if WHISPER["modelo"] is None or WHISPER["nombre"] != nombre:
            WHISPER["modelo"], real, WHISPER["donde"] = wh.cargar(nombre)
            WHISPER["nombre"] = nombre
            log(f"whisper {real} en {WHISPER['donde']}")


def soltar_whisper():
    with _whisper_lock:
        WHISPER["modelo"], WHISPER["nombre"] = None, ""
    gc.collect()


# ------------------------------------------------------------------ nota final (Markdown, compatible con Obsidian)
def slug(t):
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"[^\w\s-]", "", t).strip()
    return re.sub(r"[\s_]+", "-", t)[:50] or "reunion"


def boveda_obsidian(f: Path):
    return next((d for d in [f, *f.parents] if (d / ".obsidian").is_dir()), None)


def nota_basica(s):
    """Nota armada sin IA (ningún modelo respondió): lo vivido, ordenado. Se puede rehacer con IA desde Notas."""
    pant = "\n".join(f"- **{p['t'][:5]}** · {p['titulo']}: {p['resumen']}" for p in s.pantallas if not p.get("sin_leer"))
    herr = list(dict.fromkeys(h.strip() for p in s.pantallas for h in p.get("herramientas") or [] if h.strip()))
    preg = "\n".join(f"- **{q['cual']}** — {q.get('respuesta') or ''}".rstrip(" —") for q in s.preguntas)
    trans = "\n".join(f"- {l['t'][:5]} · {l['quien']}: {l['texto']}" for l in s.lineas)
    return ("## Resumen\nEsta nota se armó **sin IA**: al terminar ningún modelo respondió. Tiene todo lo que se dijo y se vio; "
            "en Tomo › Notas puedes pulsar «Rehacer con IA» cuando tengas un modelo listo.\n\n"
            f"## Diapositivas vistas\n{pant or 'Ninguna.'}\n\n"
            f"## Herramientas, sitios y repos mencionados\n{chr(10).join('- ' + h for h in herr) or 'Ninguno.'}\n\n"
            f"## Preguntas y respuestas\n{preg or 'Ninguna.'}\n\n## Transcripción\n{trans or '(vacía)'}")


def escribir_nota(s, ruta=None):
    """Escribe la nota final (con IA; si ningún modelo responde, una básica). `ruta` = reescribir esa nota."""
    trans = "\n".join(f"[{l['t']}] {l['quien']}: {l['texto']}" for l in s.lineas)[-60000:]
    pant = "\n".join(f"- [{p['t']}] {p['titulo']}: {p['resumen']} ({', '.join(p['herramientas'])})" for p in s.pantallas)
    preg = "\n".join(f"- [{q['t']}] {q['cual']} → {q['respuesta']}" for q in s.preguntas)
    pedido = (f"Tema: {s.tema or 'no indicado'}\nFecha: {datetime.fromtimestamp(s.inicio):%Y-%m-%d %H:%M}\n\n"
              f"{perfil()}\nPROYECTOS DEL USUARIO:\n{leer_ajustes().get('proyectos') or '(no indicó)'}\n\nDIAPOSITIVAS VISTAS:\n{pant or '(ninguna)'}\n\n"
              f"PREGUNTAS QUE SURGIERON:\n{preg or '(ninguna)'}\n\nTRANSCRIPCIÓN:\n{trans}")
    mensajes = [{"role": "system", "content": NOTAS}, {"role": "user", "content": pedido}]
    con_ia = True
    try:
        try:
            cuerpo = llamar("texto", mensajes, max_tokens=6000, timeout=240)
        except Exception as e:
            log("el modelo de texto falló para la nota, pruebo el de visión:", e)
            cuerpo = llamar("vision", mensajes, max_tokens=6000, timeout=240)
        cuerpo = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", cuerpo.strip())
        cuerpo = re.sub(r"^(##[^(\n]+?)\s*\([^\n]*\)\s*$", r"\1", cuerpo, flags=re.M)   # modelos chicos copian «(3-6 líneas)»
        if not cuerpo:
            raise RuntimeError("respuesta vacía")
    except Exception as e:
        log("ningún modelo escribió la nota; la armo sin IA:", e)
        cuerpo, con_ia = nota_basica(s), False
    CURSOS = carpeta_notas()
    CURSOS.mkdir(parents=True, exist_ok=True)
    fecha = datetime.fromtimestamp(s.inicio)
    nueva = ruta is None
    if nueva:
        nombre = f"{fecha:%Y-%m-%d}-{slug(s.tema or 'reunion')}"
        ruta = CURSOS / f"{nombre}.md"
        n = 2
        while ruta.exists():
            ruta = CURSOS / f"{nombre}-{n}.md"
            n += 1
    ruta = Path(ruta)
    titulo = s.tema or "Reunión"
    minutos = int(((s.fin or time.time()) - s.inicio) // 60)
    ruta.write_text(
        f"---\ntags: [curso, reunion]\nfecha: {fecha:%Y-%m-%d}\ntomo_reunion: {s.id}\ntomo_ia: {'si' if con_ia else 'no'}\n---\n"
        f"# {titulo} — {fecha:%Y-%m-%d}\n\n"
        f"Notas de Tomo · {minutos} min · {len(s.lineas)} frases · {len(s.preguntas)} preguntas. "
        f"Índice: [[Indice|Reuniones]].\n\n{cuerpo}\n\n"
        f"## Archivos\n- Transcripción: `{s.carpeta}/transcripcion.md`\n"
        f"- Sugerencias: `{s.carpeta}/sugerencias.md`\n- Capturas: `{s.carpeta}/pantallas/`\n",
        encoding="utf-8")
    if nueva:
        m = re.search(r"## Resumen\s*\n+(.+)", cuerpo)
        de_que = re.sub(r"^[-*\s]+|\*\*", "", m.group(1).strip() if m else titulo).replace("|", "/")[:110]
        indice = CURSOS / "Indice.md"
        if not indice.exists():
            indice.write_text("---\ntags: [indice]\n---\n# Cursos y reuniones\n\n"
                              "Notas que deja Tomo al terminar cada "
                              "curso o reunión: resumen, herramientas mencionadas, ideas para mis proyectos y preguntas.\n\n"
                              "| Nota | De qué trató | Fecha |\n|---|---|---|\n", encoding="utf-8")
        with indice.open("a", encoding="utf-8") as f:
            f.write(f"| [[{ruta.stem}]] | {de_que} | {fecha:%Y-%m-%d} |\n")
    log("nota:", ruta, "" if con_ia else "(sin IA)")
    return str(ruta)


def rehacer_nota(ruta: Path):
    """Vuelve a escribir con IA la nota de una reunión (p. ej. una que salió «sin IA»)."""
    m = re.search(r"^tomo_reunion:\s*(\S+)", ruta.read_text(encoding="utf-8"), re.M)
    if not m:
        raise RuntimeError("esta nota no la escribió Tomo para una reunión")
    f = RAIZ / f"reunion-{m.group(1)}" / "sesion.json"
    if not f.exists():
        raise RuntimeError("ya no está la carpeta de esa reunión")
    d = json.loads(f.read_text(encoding="utf-8"))
    ini = datetime.fromisoformat(d["inicio"]).timestamp()
    fin = datetime.fromisoformat(d["fin"]).timestamp() if d.get("fin") else ini + 60 * (d.get("minutos") or 0)
    s = SimpleNamespace(id=d["id"], tema=d.get("tema", ""), inicio=ini, fin=fin, lineas=d.get("lineas") or [],
                        pantallas=d.get("pantallas") or [], preguntas=list(reversed(d.get("preguntas") or [])), carpeta=f.parent)
    escribir_nota(s, ruta)
    if "tomo_ia: no" in ruta.read_text(encoding="utf-8"):
        raise RuntimeError("ningún modelo de IA respondió; la nota sigue igual")


# ------------------------------------------------------------------ notas dentro de Tomo (sin depender de Obsidian)
def plano(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def raices_notas():
    return [r.resolve() for r in (carpeta_notas(), RAIZ)]


def nota_permitida(f: Path):
    return f.suffix.lower() == ".md" and any(r == f.parent or r in f.parents for r in raices_notas())


def _frente(texto):
    m = re.match(r"---\n(.*?)\n---\n", texto, re.S)
    return dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M)) if m else {}


def archivos_notas(limite=400):
    raiz = carpeta_notas()
    if not raiz.is_dir():
        return []
    fs = [f for f in raiz.rglob("*.md") if not any(x.startswith(".") for x in f.relative_to(raiz).parts)]
    return sorted(fs, key=lambda f: f.stat().st_mtime, reverse=True)[:limite]


def resumen_corto(cuerpo):
    """Lo primero que vale la pena leer: el «Resumen» de las notas de Tomo o el primer párrafo."""
    m = re.search(r"^## Resumen[^\n]*\n+(.+?)(?=\n## |\Z)", cuerpo, re.S | re.M)
    base = m.group(1) if m else "\n".join(l for l in cuerpo.splitlines()
                                          if l.strip() and not l.startswith(("#", "Notas de Tomo", "---")))
    base = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", base)
    base = re.sub(r"\[\[([^\]]+)\]\]|\[([^\]]+)\]\([^)]*\)", lambda x: x.group(1) or x.group(2), base)
    base = re.sub(r"^\s*([-+*]|\d+[.)])\s+(\[.\]\s*)?", "", base, flags=re.M)
    return re.sub(r"\s+", " ", re.sub(r"[*`>#|_]+", " ", base)).strip()[:170]


def listar_notas(q=""):
    terminos = plano(q).split()
    out = []
    for f in archivos_notas():
        try:
            texto = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cuerpo = re.sub(r"^---\n.*?\n---\n", "", texto, flags=re.S)
        extracto = ""
        if terminos:
            p = plano(f.stem + "\n" + texto)
            if not all(t in p for t in terminos):
                continue
            i = plano(cuerpo).find(terminos[0])
            if i >= 0:
                extracto = ("…" if i > 60 else "") + cuerpo[max(0, i - 60):i + 120].replace("\n", " ").strip() + "…"
        if not extracto:
            extracto = resumen_corto(cuerpo)
        fr = _frente(texto)
        tit = re.search(r"^# (.+)$", cuerpo, re.M)
        out.append({"ruta": str(f), "nombre": f.stem, "titulo": tit.group(1).strip() if tit else f.stem,
                    "fecha": fr.get("fecha") or datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
                    "mtime": f.stat().st_mtime, "extracto": extracto, "tomo": bool(fr.get("tomo_reunion")),
                    "sin_ia": fr.get("tomo_ia") == "no", "carpeta": str(f.parent.relative_to(carpeta_notas())) if f.parent != carpeta_notas() else ""})
    return out


def buscar_nota(nombre):
    """[[Wikilink]] → archivo (como Obsidian: por nombre, sin importar la carpeta ni mayúsculas)."""
    n = plano(nombre.split("|")[0].split("#")[0].strip().rsplit("/", 1)[-1].removesuffix(".md"))   # [[Carpeta/Nota]] también
    return next((f for f in archivos_notas(5000) if plano(f.stem) == n), None)


_apps = {}


def app_para(tipo):
    """Nombre de la app que abre ese tipo (text/markdown, inode/directory, x-scheme-handler/obsidian) o ""."""
    if tipo in _apps and time.time() - _apps[tipo][0] < 60:
        return _apps[tipo][1]
    nombre = ""
    try:
        d = subprocess.run(["xdg-mime", "query", "default", tipo], capture_output=True, text=True, timeout=5,
                           env=pantalla.entorno()).stdout.strip()
        if d:
            bases = [Path.home() / ".local/share", Path.home() / ".local/share/flatpak/exports/share",
                     Path("/var/lib/flatpak/exports/share"),
                     *map(Path, (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"))]
            for b in bases:
                f = b / "applications" / d
                if f.is_file():
                    m = re.search(r"^Name=(.+)$", f.read_text(errors="replace"), re.M)
                    nombre = m.group(1).strip() if m else d.removesuffix(".desktop")
                    break
    except Exception:
        nombre = ""
    _apps[tipo] = (time.time(), nombre)
    return nombre


def como_abrir(f: Path):
    """Con qué se puede abrir la nota fuera de Tomo (solo lo que de verdad está instalado)."""
    return {"obsidian": bool(boveda_obsidian(f) and app_para("x-scheme-handler/obsidian")),
            "app": app_para("text/markdown") or app_para("text/plain"),
            "carpeta": app_para("inode/directory")}


def datos_nota(f: Path):
    texto = f.read_text(encoding="utf-8", errors="replace")
    fr = _frente(texto)
    return {"ruta": str(f), "nombre": f.stem, "texto": texto, "mtime": f.stat().st_mtime, "abrir": como_abrir(f),
            "obsidian": como_abrir(f)["obsidian"], "reunion": fr.get("tomo_reunion", ""), "sin_ia": fr.get("tomo_ia") == "no",
            "carpeta_notas": str(carpeta_notas())}


# ------------------------------------------------------------------ historial
def historial():
    out = []
    for f in sorted(RAIZ.glob("reunion-*/sesion.json"), reverse=True)[:60]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: d.get(k) for k in ("id", "tema", "inicio", "minutos", "n_lineas", "nota", "estado")}
                       | {"n_preguntas": len(d.get("preguntas") or [])})
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ HTTP
SESION = {"actual": None}
_sesion_lock = threading.Lock()


def estado():
    s = SESION["actual"]
    with LIBRE["lock"]:
        libres = [publico(e) for e in reversed(LIBRE["ejercicios"])]
    comp = pantalla.estado_compartir() if pantalla.usar_compartir() else None
    web = max((f.stat().st_mtime for f in WEB.glob("*") if f.is_file()), default=0)   # la app se recarga sola si cambia
    return {"en_linea": True, "version": VERSION, "web": int(web), "ajustes": leer_ajustes(), "modelos_listos": modelos.hay_algo(), "compartir": comp,
            "avisos_sistema": bool(leer_ajustes().get("globitos", True) and shutil.which("notify-send")),
            "actualizacion": (ACTUALIZACION["disponible"] or {}).get("version", ""), "act_estado": ACTUALIZACION["estado"], "sesion": s.resumen() if s else None, "ejercicios_libres": libres}


# ------------------------------------------------------------------ novedades y actualizaciones
REPO = os.environ.get("TOMO_REPO", "sJonathan15/tomo")
ACTUALIZACION = {"disponible": None, "revisado": 0.0, "estado": "", "error": ""}


def version_t(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def novedades_locales():
    try:
        return json.loads((BASE / "novedades.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def revisar_actualizacion():
    """Cada 6 h mira novedades.json en GitHub; si trae una versión mayor, la app muestra «Actualizar»."""
    while True:
        try:
            url = f"https://raw.githubusercontent.com/{REPO}/main/tomo/novedades.json"
            remotas = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tomo"}), timeout=15))
            nuevas = [n for n in remotas if version_t(n.get("version")) > version_t(VERSION)]
            ACTUALIZACION.update(disponible=({"version": max((n["version"] for n in nuevas), key=version_t), "novedades": nuevas}
                                             if nuevas else None), revisado=time.time())
        except Exception as e:
            ACTUALIZACION["revisado"] = time.time()
            log("no pude revisar actualizaciones:", e)
        time.sleep(6 * 3600)


def actualizar():
    """git pull + dependencias; luego el proceso termina y systemd lo vuelve a abrir con la versión nueva."""
    raiz = BASE.parent
    ACTUALIZACION.update(estado="actualizando", error="")
    try:
        if not (raiz / ".git").exists():
            raise RuntimeError("esta copia no vino de git: vuelve a correr el instalador (install.sh)")
        subprocess.run(["git", "-C", str(raiz), "pull", "--ff-only"], check=True, capture_output=True, text=True, timeout=120)
        req = raiz / "requirements.txt"
        if req.exists():
            subprocess.run([os.sys.executable, "-m", "pip", "install", "-q", "-r", str(req)], check=True,
                           capture_output=True, text=True, timeout=900)
        ACTUALIZACION["estado"] = "reiniciando"
        log("actualizado; reinicio")
        s = SESION["actual"]
        if s and s.estado in ("cargando", "escuchando", "pausado"):
            s.guardar()        # la reunión se retoma sola al volver (reanudar)
        threading.Timer(1.5, lambda: os._exit(0)).start()
    except subprocess.CalledProcessError as e:
        ACTUALIZACION.update(estado="error", error=(e.stderr or e.stdout or str(e))[-400:])
    except Exception as e:
        ACTUALIZACION.update(estado="error", error=str(e))


_github = {"t": 0.0, "datos": {}}


def github():
    """Estrellas del repo (cache 1 h) para el botón «Danos una estrella»."""
    if time.time() - _github["t"] > 3600:
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://api.github.com/repos/{REPO}", headers={"User-Agent": "tomo", "Accept": "application/vnd.github+json"}), timeout=10))
            _github["datos"] = {"estrellas": d.get("stargazers_count", 0), "url": d.get("html_url") or f"https://github.com/{REPO}"}
        except Exception:
            _github["datos"] = _github["datos"] or {"estrellas": None, "url": f"https://github.com/{REPO}"}
        _github["t"] = time.time()
    return _github["datos"]


# paquete que trae cada cosa, por gestor: apt · dnf · pacman · zypper
PAQUETES = {
    "pipewire": ("pipewire-bin", "pipewire-utils", "pipewire", "pipewire-tools"),
    "pactl": ("pulseaudio-utils", "pulseaudio-utils", "libpulse", "pulseaudio-utils"),
    "notify-send": ("libnotify-bin", "libnotify", "libnotify", "libnotify-tools"),
    "xdg-open": ("xdg-utils", "xdg-utils", "xdg-utils", "xdg-utils"),
    "gi": ("python3-gi", "python3-gobject", "python-gobject", "python3-gobject"),
    "gstreamer": ("gstreamer1.0-pipewire gstreamer1.0-plugins-good gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0",
                  "pipewire-gstreamer gstreamer1-plugins-good", "gst-plugin-pipewire gst-plugins-good",
                  "gstreamer-plugin-pipewire gstreamer-plugins-good typelib-1_0-Gst-1_0"),
    "grim": ("grim",) * 4, "spectacle": ("kde-spectacle", "spectacle", "spectacle", "spectacle"), "maim": ("maim",) * 4,
}


def faltantes():
    """Lo que falta en el equipo, para qué sirve y el comando exacto de su distro."""
    gestor = next((i for i, g in enumerate(("apt-get", "dnf", "pacman", "zypper")) if shutil.which(g)), None)
    falta = []

    def f(clave, que, para, obligatorio=False):
        paq = PAQUETES[clave][gestor] if gestor is not None else clave
        falta.append({"que": que, "para": para, "paquete": paq, "obligatorio": obligatorio})
    motor = motor_audio()
    if motor == "nada":
        f("pipewire", "Audio del sistema (PipeWire)", "oír la reunión", True)
    elif motor == "pipewire" and not shutil.which("pactl"):
        f("pactl", "pactl", "oír solo la app de reunión (sin él, Tomo oye todo lo que suena)")
    if not shutil.which("xdg-open"):
        f("xdg-open", "xdg-open", "abrir enlaces y carpetas desde Tomo")
    if leer_ajustes().get("globitos", True) and not shutil.which("notify-send"):
        f("notify-send", "Avisos del escritorio", "avisarte de las preguntas (sin esto, Tomo usa los avisos del navegador)")
    esc = pantalla.escritorio()
    if pantalla.usar_compartir():
        if not pantalla.hay_portal():
            f("gi", "Portal del escritorio (python3-gi)", "mirar la pantalla en GNOME", True)
        elif not pantalla.gstreamer_listo():
            f("gstreamer", "GStreamer con PipeWire", "compartir la pantalla de la reunión sin destellos", True)
    elif not pantalla.herramientas():
        clave = {"kde": "spectacle", "hyprland": "grim", "sway": "grim", "niri": "grim", "wayland": "grim"}.get(esc, "maim")
        f(clave, f"Captura de pantalla ({clave})", "mirar la pantalla de la reunión", True)
    cmd = ""
    paqs = " ".join(x["paquete"] for x in falta)
    if paqs and gestor is not None:
        cmd = ("sudo apt install ", "sudo dnf install ", "sudo pacman -S --needed ", "sudo zypper install ")[gestor] + paqs
    return {"faltan": falta, "comando": cmd}


def sistema():
    """Lo que Tomo encontró en este equipo (para la pantalla de configuración)."""
    herramientas = {h: bool(shutil.which(h)) for h in ("pw-record", "pw-link", "pw-dump", "pactl", "parec", "notify-send", "xdg-open")}
    return {"version": VERSION, "escritorio": pantalla.escritorio(), "capturas": pantalla.herramientas(),
            "monitores": pantalla.monitores(), "audio": herramientas, "pipewire": all(herramientas[h] for h in ("pw-record", "pw-link", "pw-dump")) or herramientas["parec"],
            "motor_audio": motor_audio(), **faltantes(), "cuda": wh.hay_cuda(), "whisper_sugerido": wh.modelo_sugerido(), "vram_gb": modelos.vram_gb(), "ram_gb": modelos.ram_gb(),
            "compartir": pantalla.estado_compartir(), "gstreamer": pantalla.gstreamer_listo() if pantalla.usar_compartir() else None,
            "datos": str(RAIZ), "notas": str(carpeta_notas()), "config": str(CONFIG), "puerto": PUERTO,
            "whisper": {"cargado": WHISPER["nombre"], "donde": WHISPER["donde"]}, "repo": REPO,
            "ollama": modelos.estado_local()}


def prompt_agente():
    """Texto para pegarle a un agente de IA (Claude Code, Codex, Gemini CLI…) y que configure Tomo con el usuario."""
    si = sistema()
    return f"""Ayúdame a configurar **Tomo** (copiloto de reuniones de MIRAI) en mi Linux. Tomo corre en http://127.0.0.1:{PUERTO}.

Lo que Tomo ya detectó en mi equipo:
- Escritorio: {si['escritorio']} · capturas con: {', '.join(si['capturas']) or 'NINGUNA (falta instalar una)'}
- PipeWire: {'sí' if si['pipewire'] else 'NO (faltan pw-record/pw-link/pw-dump)'} · tarjeta NVIDIA con CUDA: {'sí' if si['cuda'] else 'no'} · VRAM {si['vram_gb']} GB · RAM {si['ram_gb']} GB
- Whisper sugerido: {si['whisper_sugerido']}

Qué necesito que hagas, paso a paso y preguntándome antes de instalar nada:
1. Revisa que no falte nada: `curl -s http://127.0.0.1:{PUERTO}/api/sistema`. Si falta una herramienta de captura o PipeWire, dime el comando para mi distro.
2. Modelos de IA. Tomo usa una cadena: primero las APIs (en orden) y al final el modelo local. Mira cómo está: `curl -s http://127.0.0.1:{PUERTO}/api/modelos` (las claves nunca se muestran completas).
   - Para agregar una API compatible con OpenAI (Gemini, NVIDIA, OpenRouter, Groq, OpenAI u otra), pídeme la clave y ejecuta:
     `curl -s -X POST http://127.0.0.1:{PUERTO}/api/modelos/proveedor -H 'Content-Type: application/json' -d '{{"tipo":"gemini","api_key":"<CLAVE>"}}'`
     (tipos: gemini, nvidia, openrouter, groq, openai, otro; con "otro" manda también "base_url", "texto" y "vision" = nombres de modelo).
   - Pruébala: `curl -s -X POST http://127.0.0.1:{PUERTO}/api/modelos/probar -H 'Content-Type: application/json' -d '{{"id":"<id que devolvió>"}}'`
   - Modelo local (Ollama): si tengo Ollama, mira qué modelos con visión tengo en la respuesta de /api/modelos («ollama.modelos», campo "vision"). Si no hay ninguno, recomiéndame el de «ollama.recomendado» y descárgalo con
     `curl -s -X POST http://127.0.0.1:{PUERTO}/api/modelos/descargar -H 'Content-Type: application/json' -d '{{"modelo":"<nombre>"}}'`. Si no tengo Ollama, explícame cómo instalarlo (https://ollama.com/download).
3. Elige con mi permiso la pantalla donde estará la reunión: `curl -s http://127.0.0.1:{PUERTO}/api/monitores` y luego
   `curl -s -X POST http://127.0.0.1:{PUERTO}/api/ajustes -H 'Content-Type: application/json' -d '{{"monitor":"<nombre>"}}'`.
4. Perfil (opcional, mejora las respuestas): `curl -s -X POST http://127.0.0.1:{PUERTO}/api/ajustes -H 'Content-Type: application/json' -d '{{"nombre":"…","rol":"…","proyectos":"…"}}'`.
5. Al final abre la app (`tomo abrir`) y dime que pulse «Empezar» en una reunión de prueba.

Reglas: no muestres mis claves en pantalla, no edites archivos de Tomo a mano si la API lo permite, y explícame todo en lenguaje simple.
La configuración vive en {si['config']} (modelos.json con permisos 600)."""


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, d, codigo=200):
        cuerpo = json.dumps(d, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    def _ajeno(self, post=False):
        """Tomo solo atiende a su propia app y a la terminal: otra página abierta en el navegador no puede
        leer las reuniones ni cambiar la configuración (p. ej. poner una API suya para llevarse lo que se dice)."""
        propios = {f"127.0.0.1:{PUERTO}", f"localhost:{PUERTO}"}
        if (self.headers.get("Host") or "").lower() not in propios:
            return True
        origen = self.headers.get("Origin")
        if origen and origen.lower().removeprefix("http://") not in propios:
            return True
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return True
        return post and "application/json" not in (self.headers.get("Content-Type") or "")

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if self._ajeno() and ruta.startswith(("/api/", "/captura/")):
            return self._json({"error": "solo la app de Tomo puede pedir esto"}, 403)
        if ruta == "/api/estado":
            return self._json(estado())
        if ruta == "/api/historial":
            return self._json(historial())
        if ruta == "/api/modelos":
            return self._json(modelos.publico())
        if ruta == "/api/sistema":
            return self._json(sistema())
        if ruta == "/api/notas":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            return self._json({"notas": listar_notas((q.get("q") or [""])[0]), "carpeta": str(carpeta_notas())})
        if ruta == "/api/nota":
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            if q.get("nombre"):
                f = buscar_nota(q["nombre"][0])
                if not f:
                    return self._json({"error": f"no hay una nota llamada «{q['nombre'][0]}»"}, 404)
            else:
                f = Path((q.get("ruta") or [""])[0]).resolve()
            if not f.is_file() or not nota_permitida(f):
                return self._json({"error": "esa nota no se puede leer"}, 400)
            return self._json(datos_nota(f))
        if ruta == "/api/github":
            return self._json(github())
        if ruta == "/api/novedades":
            return self._json({"version": VERSION, "locales": novedades_locales(), "actualizacion": ACTUALIZACION})
        if ruta == "/api/prompt-agente":
            return self._json({"prompt": prompt_agente()})
        if ruta == "/api/monitores":
            return self._json({"monitores": pantalla.monitores(), "elegido": leer_ajustes().get("monitor", "")})
        m = re.fullmatch(r"/api/monitor/([\w.-]+)\.jpg", ruta)
        if m or ruta == "/api/vivo.jpg":
            try:
                tmp = RAIZ / f".vista-{threading.get_ident()}.png"
                datos = (pantalla.vivo(tmp, leer_ajustes().get("monitor") or "") if ruta == "/api/vivo.jpg"
                         else pantalla.miniatura(tmp, m.group(1)))
            except Exception as e:
                log("vista:", e)
                datos = None
            if not datos:
                return self._json({"error": "no se pudo capturar"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
            return
        if ruta == "/api/lineas":
            s = SESION["actual"]
            q = urllib.parse.parse_qs(self.path.partition("?")[2])
            n = int((q.get("desde") or ["0"])[0] or 0)
            return self._json({"id": s.id if s else "", "desde": n, "lineas": s.lineas_desde(n) if s else []})
        m = re.fullmatch(r"/api/reunion/(reunion-)?([\w-]+)", ruta)
        if m:
            f = RAIZ / f"reunion-{m.group(2)}" / "sesion.json"
            return self._json(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else self._json({"error": "no existe"}, 404)
        m = re.fullmatch(r"/captura/([\w-]+)/([\w-]+\.(?:jpg|png|webp))", ruta)
        if m:
            if m.group(1) == "ejercicios":
                return self._archivo(LIBRE["carpeta"] / m.group(2))
            return self._archivo(RAIZ / f"reunion-{m.group(1)}" / "pantallas" / m.group(2))
        if ruta == "/":
            ruta = "/index.html"
        destino = (WEB / ruta.lstrip("/")).resolve()
        if WEB.resolve() in destino.parents and destino.is_file():
            return self._archivo(destino)
        self._json({"error": "no existe"}, 404)

    def _archivo(self, f: Path):
        if not f.is_file():
            return self._json({"error": "no existe"}, 404)
        tipos = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".jpg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml", ".png": "image/png"}
        datos = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipos.get(f.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self):
        if self._ajeno(post=True):
            return self._json({"error": "solo la app de Tomo puede pedir esto (usa Content-Type: application/json)"}, 403)
        ruta, d = self.path.split("?")[0], self._cuerpo()
        s = SESION["actual"]
        activa = s is not None and s.estado in ("cargando", "escuchando", "pausado")
        if ruta == "/api/empezar":
            with _sesion_lock:
                if SESION["actual"] and SESION["actual"].estado in ("cargando", "escuchando", "pausado"):
                    return self._json({"error": "ya hay una reunión en curso"}, 409)
                ajustes = leer_ajustes()
                if "microfono" in d:
                    ajustes["microfono"] = bool(d["microfono"])
                    guardar_ajustes(ajustes)
                SESION["actual"] = Sesion(str(d.get("tema", "")), ajustes)
                SESION["actual"].empezar()
            return self._json(estado())
        if ruta == "/api/abrir":          # abrir fuera de Tomo, solo con apps que existan
            f = Path(str(d.get("ruta", ""))).resolve()
            if not f.is_file() or not nota_permitida(f):
                return self._json({"error": "esa ruta no se puede abrir"}, 400)
            en, puede = d.get("en", "app"), como_abrir(f)
            if en == "obsidian" and puede["obsidian"]:   # vault + file: lo más fiable (también con Obsidian en Flatpak)
                boveda = boveda_obsidian(f)
                rel = f.relative_to(boveda).with_suffix("").as_posix()
                uri = f"obsidian://open?vault={urllib.parse.quote(boveda.name)}&file={urllib.parse.quote(rel)}"
            elif en == "carpeta" and puede["carpeta"]:
                uri = str(f.parent)
            elif en == "app" and puede["app"]:
                uri = str(f)
            else:
                return self._json({"error": "no hay una app instalada para eso; la nota se puede ver y editar aquí en Tomo"}, 400)
            if not shutil.which("xdg-open"):
                return self._json({"error": "falta xdg-open (paquete xdg-utils)"}, 400)
            p = subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=pantalla.entorno())
            try:
                if p.wait(timeout=3):
                    return self._json({"error": "el sistema no pudo abrirla"}, 400)
            except subprocess.TimeoutExpired:
                pass      # sigue abriéndose
            return self._json({"ok": True})
        if ruta == "/api/nota":           # guardar lo editado en Tomo, o crear una nota nueva
            if d.get("nuevo") is not None:
                raiz = carpeta_notas()
                raiz.mkdir(parents=True, exist_ok=True)
                titulo = str(d["nuevo"]).strip()[:120] or "Nota nueva"
                f = raiz / f"{datetime.now():%Y-%m-%d}-{slug(titulo)}.md"
                n = 2
                while f.exists():
                    f = raiz / f"{datetime.now():%Y-%m-%d}-{slug(titulo)}-{n}.md"
                    n += 1
                f.write_text(f"# {titulo}\n\n", encoding="utf-8")
                return self._json(datos_nota(f))
            f = Path(str(d.get("ruta", ""))).resolve()
            if not f.is_file() or not nota_permitida(f):
                return self._json({"error": "esa nota no se puede guardar"}, 400)
            if d.get("mtime") and abs(f.stat().st_mtime - float(d["mtime"])) > 0.01:
                return self._json({"error": "la nota cambió fuera de Tomo; recárgala antes de guardar", "conflicto": True}, 409)
            tmp = f.with_name(f".{f.name}.tomo-tmp")
            tmp.write_text(str(d.get("texto", "")), encoding="utf-8")
            tmp.replace(f)
            return self._json({"ok": True, "mtime": f.stat().st_mtime})
        if ruta == "/api/nota/rehacer":
            f = Path(str(d.get("ruta", ""))).resolve()
            if not f.is_file() or not nota_permitida(f):
                return self._json({"error": "esa nota no se puede rehacer"}, 400)
            try:
                rehacer_nota(f)
            except Exception as e:
                return self._json({"error": str(e)}, 400)
            return self._json(datos_nota(f))
        if ruta == "/api/compartir":
            pantalla.compartir(elegir=bool(d.get("elegir")))
            return self._json(pantalla.estado_compartir())
        if ruta == "/api/actualizar":
            threading.Thread(target=actualizar, daemon=True).start()
            return self._json({"ok": True})
        if ruta.startswith("/api/modelos/"):
            accion = ruta.rsplit("/", 1)[1]
            if accion == "proveedor":
                return self._json({"id": modelos.guardar_proveedor(d), **modelos.publico()})
            if accion == "borrar":
                modelos.borrar_proveedor(str(d.get("id")))
            elif accion == "mover":
                modelos.mover_proveedor(str(d.get("id")), int(d.get("paso", 0)))
            elif accion == "probar":
                ok, msj = modelos.probar(str(d.get("id")))
                return self._json({"ok": ok, "mensaje": msj})
            elif accion == "listar":
                return self._json({"modelos": modelos.listar_remotos(str(d.get("id")))})
            elif accion == "local":
                modelos.guardar_local(d)
            elif accion == "descargar":
                modelos.descargar(str(d.get("modelo") or modelos.recomendado()["nombre"]))
            else:
                return self._json({"error": "acción desconocida"}, 404)
            return self._json(modelos.publico())
        if ruta == "/api/resolver":
            if not (str(d.get("texto", "")).strip() or d.get("imagenes")):
                return self._json({"error": "escribe el enunciado o pega una imagen"}, 400)
            eid = resolver(d)
            return self._json(estado() | {"ejercicio": eid})
        if ruta == "/api/ajustes":
            a = leer_ajustes()
            a.update({k: v for k, v in d.items() if k in AJUSTES_DEF})
            guardar_ajustes(a)
            if "monitor" in d and activa:      # pantalla nueva: mirarla ya
                s.huella = None
                threading.Thread(target=s.mirar_pantalla, kwargs={"forzar": True}, daemon=True).start()
            return self._json(estado())
        if not activa and ruta != "/api/respuesta":
            return self._json({"error": "no hay una reunión en curso"}, 409)
        if ruta == "/api/pausar":
            with s.lock:
                s.estado = "pausado" if s.estado == "escuchando" else s.estado
            s.guardar()
        elif ruta == "/api/reanudar":
            with s.lock:
                s.estado = "escuchando" if s.estado == "pausado" else s.estado
            s.guardar()
        elif ruta == "/api/terminar":
            s.terminar()
        elif ruta == "/api/tema":
            with s.lock:
                s.tema = str(d.get("tema", s.tema))
                s.prompt = f"Reunión o curso. {s.tema}".strip()
            s.guardar()
        elif ruta == "/api/ayudame":
            threading.Thread(target=s.ayudar, kwargs={"forzado": True}, daemon=True).start()
        elif ruta == "/api/pantalla":
            threading.Thread(target=s.mirar_pantalla, kwargs={"forzar": True}, daemon=True).start()
        elif ruta == "/api/respuesta":
            q = s.pregunta(str(d.get("id"))) if s else None
            if not q or d.get("variante") not in ("otra", "corta"):
                return self._json({"error": "pregunta o variante no válida"}, 400)
            threading.Thread(target=s.responder, args=(q, d["variante"]), daemon=True).start()
        else:
            return self._json({"error": "ruta desconocida"}, 404)
        self._json(estado())


def reanudar():
    """Si el motor se reinició con una reunión en curso (hace < 20 min), la retoma: misma carpeta y datos."""
    for f in sorted(RAIZ.glob("reunion-*/sesion.json"), reverse=True)[:1]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            vivo = d.get("estado") in ("cargando", "escuchando", "pausado") or (d.get("estado") == "error" and not d.get("fin"))
            if vivo and time.time() - f.stat().st_mtime < 1800:
                s = Sesion(d.get("tema", ""), leer_ajustes(), previa=d)
                SESION["actual"] = s
                s.empezar()
                log("reunión retomada:", s.id, len(s.lineas), "frases")
        except Exception:
            traceback.print_exc()


def main():
    RAIZ.mkdir(parents=True, exist_ok=True)
    reanudar()
    if os.environ.get("TOMO_SIN_ACTUALIZAR") != "1":
        threading.Thread(target=revisar_actualizacion, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    srv.daemon_threads = True
    log(f"Tomo en http://127.0.0.1:{PUERTO}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
