"""Whisper local (faster-whisper): cargar el modelo en la tarjeta de video si se puede, si no en la CPU,
y filtrar las frases que Whisper inventa sobre el silencio."""
import ctypes
import glob
import os
import re
import sys
from pathlib import Path

INVENTADAS = ("gracias por ver", "suscríbete", "subtítulos", "amara.org", "gracias.", "y ya está",
              "thanks for watching", "subscribe")


def limpiar(texto):
    """«no, no, no, no…» → «no, no…»: Whisper entra en bucle con palabras repetidas."""
    return re.sub(r"\b(\w+(?: \w+)?)([,.]? \1\b){2,}[,.]?", r"\1, \1…", texto.strip())


def palabras(texto):
    return set(re.findall(r"\w{3,}", texto.lower()))


def fiable(s, prompt=""):
    """Descarta segmentos que suelen ser alucinaciones."""
    t = s.text.strip().lower()
    if not t or s.no_speech_prob > 0.6 or s.avg_logprob < -1.0 or s.compression_ratio > 2.4:
        return False
    p = palabras(t)
    if prompt and p and len(p & palabras(prompt)) / len(p) > 0.6:     # en silencio «lee» el contexto dado
        return False
    sin_signos = re.sub(r"[^\w ]", "", t).strip()
    return not any(sin_signos == f.strip(".") or (f.endswith("ver") and f in t) or (len(t) < 25 and f in t)
                   for f in INVENTADAS)


def hay_cuda():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def modelo_sugerido():
    """GPU → large-v3-turbo (≈1,6 GB, el mejor en español). Solo CPU → small (rápido) o medium si hay RAM."""
    return "large-v3-turbo" if hay_cuda() else modelo_cpu()


def modelo_cpu():
    """Para la CPU: medium si el equipo es holgado (16 GB y 8 núcleos), si no small."""
    try:
        kb = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1])
    except Exception:
        kb = 0
    return "medium" if kb > 15 * 1024 * 1024 and (os.cpu_count() or 2) >= 8 else "small"


def cargar(nombre="auto"):
    """GPU primero (int8_float16); si no hay tarjeta o memoria, CPU (int8)."""
    if nombre in ("", "auto"):
        nombre = modelo_sugerido()
    # cuBLAS/cuDNN instalados como paquetes pip (nvidia-cublas-cu12, nvidia-cudnn-cu12): cargarlos antes
    nvidia = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "nvidia"
    for patron in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*", "cudnn/lib/libcudnn*.so.*"):
        for lib in sorted(glob.glob(str(nvidia / patron))):
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
    from faster_whisper import WhisperModel
    if hay_cuda():
        try:
            return WhisperModel(nombre, device="cuda", compute_type="int8_float16"), nombre, "gpu"
        except Exception as e:
            print(f"GPU no disponible ({e}); uso CPU", file=sys.stderr, flush=True)
    if nombre.startswith("large"):
        nombre = modelo_cpu()
    return WhisperModel(nombre, device="cpu", compute_type="int8", cpu_threads=max(2, (os.cpu_count() or 4) - 2)), nombre, "cpu"
