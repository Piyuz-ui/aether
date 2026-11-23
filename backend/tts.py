"""AETHER's local neural voice: Kokoro-82M text-to-speech.

Engines, in order of preference (picked automatically, never crashing the backend):
  1. kokoro-mlx   Kokoro via mlx-audio on the Apple Silicon GPU. Needs `pip install mlx-audio
                  "misaki[en]"`, and misaki's English front end needs spaCy (Python ≤ 3.13 today).
  2. kokoro-onnx  Kokoro via ONNX Runtime on the CPU. ~6× faster than real time on an M-series
                  Mac. Its model files (~350 MB) download automatically on first use.
  3. fallback     Nothing installed: the web app falls back to the browser's built-in voice.

The model loads lazily on the first request and then stays warm in memory, so each sentence
after that is synthesised in a fraction of a second.
"""
from __future__ import annotations

import asyncio
import io
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 24000
MIN_SPEED, MAX_SPEED = 0.85, 1.25
MAX_CHARS = 2000

VOICES: dict[str, dict[str, str]] = {
    "af_heart": {"name": "Heart", "description": "Warm & natural", "accent": "American"},
    "af_bella": {"name": "Bella", "description": "Soft & soothing", "accent": "American"},
    "bm_george": {"name": "George", "description": "Calm British", "accent": "British"},
    "bf_emma": {"name": "Emma", "description": "Crisp British", "accent": "British"},
}
DEFAULT_VOICE = "af_heart"

ONNX_FILES = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
MLX_MODEL = "mlx-community/Kokoro-82M-bf16"


# ───────────────────────────────────────────────────────── speech-friendly text
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF️‍]+")
_ABBREVIATIONS = ("Dr", "Mr", "Mrs", "Ms", "Prof", "Sr", "Jr", "St", "vs", "etc", "e.g", "i.e", "Fig",
                  "No", "approx", "Inc", "Ltd", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep",
                  "Sept", "Oct", "Nov", "Dec")


def prepare_for_speech(text: str) -> str:
    """Turn Markdown into something that sounds natural when read aloud."""
    t = text or ""
    t = re.sub(r"```[\s\S]*?(```|$)", " I've put the code block on screen. ", t)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # images → alt text
    t = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", t)               # [title](url) → title
    t = re.sub(r"\[([^\]]+\.(?:pdf|pptx|md|txt)) (?:p\.|slide) ?(\d+)\]", r"(\1, page \2)", t)
    t = re.sub(r"<?https?://[^\s>)\]]+>?", "", t)                     # raw URLs: never spell them out
    t = re.sub(r"\bwww\.[^\s)]+", "", t)
    # words left dangling where a URL was removed: "see the docs or ." → "see the docs."
    t = re.sub(r"\s+(?:or|and|at|via|from|on|see|visit)\s*(?=[.,;:!?]|$)", "", t, flags=re.I | re.M)
    t = re.sub(r"`([^`]+)`", r"\1", t)                               # inline code → plain
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)              # headings
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)                       # quotes
    t = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", t, flags=re.M)      # list markers
    t = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", t, flags=re.M)           # table rules
    t = t.replace("|", ", ")
    t = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", t)   # bold / italic / strike
    t = re.sub(r"[*_~#]+", " ", t)
    t = _EMOJI.sub(" ", t)
    t = (t.replace("→", " to ").replace("←", " from ").replace("≥", " at least ")
          .replace("≤", " at most ").replace("×", " times ").replace("&", " and ")
          .replace("…", "...").replace("—", ", ").replace("–", " to "))
    t = re.sub(r"\s*\n\s*\n\s*", ". ", t)                            # paragraphs → sentence breaks
    t = re.sub(r"(?<![.!?:,;])\n", ". ", t)                          # list lines → pauses
    t = t.replace("\n", " ")
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", t)
    t = re.sub(r"\.(\s*\.)+", ".", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ,;")


def split_sentences(text: str, max_chars: int = 280) -> list[str]:
    """Split into sentences without breaking at abbreviations (Dr., e.g.) or decimals (3.15)."""
    if not text.strip():
        return []
    guarded = text
    for abbr in _ABBREVIATIONS:
        guarded = re.sub(rf"\b{re.escape(abbr)}\.", abbr.replace(".", "\x00") + "\x00", guarded)
    parts = re.split(r"(?<=[.!?])[\"”')\]]*\s+(?=[\"“(\[]?[A-Z0-9])", guarded)
    out: list[str] = []
    for p in parts:
        s = p.replace("\x00", ".").strip()
        while len(s) > max_chars:  # very long sentence: break at a comma or semicolon
            cut = max(s.rfind(", ", 0, max_chars), s.rfind("; ", 0, max_chars))
            cut = cut if cut > 40 else s.rfind(" ", 0, max_chars)
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    merged: list[str] = []
    for s in out:  # tiny fragments ("Sure.") sound better joined to the next sentence
        if merged and len(merged[-1]) < 24:
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)
    return merged


def to_wav(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.clip(samples, -1.0, 1.0), sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# ────────────────────────────────────────────────────────────────── engines
class TTSError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message, self.hint = message, hint


class MLXKokoro:
    name = "kokoro-mlx"

    @staticmethod
    def available() -> bool:
        try:
            import mlx.core  # noqa: F401
            import mlx_audio  # noqa: F401
            import misaki.en  # noqa: F401  (needs spaCy; the reason this often isn't available)
            return True
        except Exception:
            return False

    def __init__(self) -> None:
        self.model: Any = None

    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        if self.model is None:
            from mlx_audio.tts.utils import load_model
            self.model = load_model(MLX_MODEL)

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        self.load()
        lang = "b" if voice.startswith("b") else "a"
        chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1)
                  for r in self.model.generate(text=text, voice=voice, speed=speed, lang_code=lang)]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


class OnnxKokoro:
    name = "kokoro-onnx"

    @staticmethod
    def available() -> bool:
        try:
            import kokoro_onnx  # noqa: F401
            import soundfile  # noqa: F401
            return True
        except Exception:
            return False

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model: Any = None
        self.download_progress: float | None = None  # 0..1 while downloading

    def files_present(self) -> bool:
        return all((self.model_dir / f).is_file() for f in ONNX_FILES)

    def ready(self) -> bool:
        return self.model is not None

    def download(self) -> None:
        import httpx
        self.model_dir.mkdir(parents=True, exist_ok=True)
        missing = [(n, u) for n, u in ONNX_FILES.items() if not (self.model_dir / n).is_file()]
        total_done = 0
        self.download_progress = 0.0
        try:
            for i, (name, url) in enumerate(missing):
                part = self.model_dir / f"{name}.part"
                with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
                    r.raise_for_status()
                    size = int(r.headers.get("content-length") or 0)
                    with part.open("wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            fh.write(chunk)
                            total_done += len(chunk)
                            if size:
                                self.download_progress = (i + min(1.0, fh.tell() / size)) / len(missing)
                part.rename(self.model_dir / name)
        except Exception as e:
            raise TTSError("Couldn't download the voice model.",
                           hint="Check your internet connection, then try again.") from e
        finally:
            self.download_progress = None

    def load(self) -> None:
        if self.model is None:
            if not self.files_present():
                self.download()
            from kokoro_onnx import Kokoro
            self.model = Kokoro(str(self.model_dir / "kokoro-v1.0.onnx"), str(self.model_dir / "voices-v1.0.bin"))

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        self.load()
        lang = "en-gb" if voice.startswith("b") else "en-us"
        samples, _sr = self.model.create(text, voice=voice, speed=speed, lang=lang)
        return np.asarray(samples, dtype=np.float32)


@dataclass
class Clip:
    wav: bytes
    seconds: float
    synth_seconds: float


class TTSService:
    """Picks the best installed engine and serialises synthesis (one model, warm in memory)."""

    def __init__(self, model_dir: Path, prefer: str = "auto"):
        self.model_dir = model_dir
        self._lock = threading.Lock()
        self.engine: MLXKokoro | OnnxKokoro | None = None
        self.error: TTSError | None = None
        self.loading = False
        if prefer in ("auto", "mlx") and MLXKokoro.available():
            self.engine = MLXKokoro()
        elif prefer in ("auto", "onnx", "mlx") and OnnxKokoro.available():
            self.engine = OnnxKokoro(model_dir)

    @property
    def engine_name(self) -> str:
        return self.engine.name if self.engine else "fallback"

    def status(self) -> dict[str, Any]:
        state = "unavailable"
        progress = None
        if self.engine is not None:
            if self.engine.ready():
                state = "ready"
            elif isinstance(self.engine, OnnxKokoro) and self.engine.download_progress is not None:
                state, progress = "downloading", round(self.engine.download_progress, 3)
            elif self.loading:
                state = "loading"
            elif isinstance(self.engine, OnnxKokoro) and not self.engine.files_present():
                state = "needs_download"
            else:
                state = "idle"  # installed, loads on first use
        return {
            "engine": self.engine_name, "state": state, "progress": progress,
            "voices": [{"id": k, **v} for k, v in VOICES.items()], "default_voice": DEFAULT_VOICE,
            "speed": {"min": MIN_SPEED, "max": MAX_SPEED, "default": 1.0}, "sample_rate": SAMPLE_RATE,
            "error": {"message": self.error.message, "hint": self.error.hint} if self.error else None,
            "hint": None if self.engine else "pip install kokoro-onnx soundfile  (then restart AETHER)",
        }

    def _load(self) -> None:
        assert self.engine is not None
        self.loading = True
        try:
            self.engine.load()
            self.error = None
        except TTSError as e:
            self.error = e
            raise
        except Exception as e:
            self.error = TTSError(f"The voice engine failed to start: {type(e).__name__}: {e}")
            raise self.error from e
        finally:
            self.loading = False

    async def warmup(self) -> dict[str, Any]:
        if self.engine is not None and not self.engine.ready():
            await asyncio.to_thread(self._locked, self._load)
        return self.status()

    def _locked(self, fn: Any, *args: Any) -> Any:
        with self._lock:
            return fn(*args)

    def _synth(self, text: str, voice: str, speed: float) -> Clip:
        assert self.engine is not None
        if not self.engine.ready():
            self._load()
        start = time.perf_counter()
        pieces = [self.engine.synth(s, voice, speed) for s in split_sentences(text, max_chars=400)]
        gap = np.zeros(int(SAMPLE_RATE * 0.12), dtype=np.float32)  # breath between sentences
        audio = np.concatenate([x for p in pieces for x in (p, gap)][:-1]) if pieces else np.zeros(0, np.float32)
        return Clip(wav=to_wav(audio), seconds=len(audio) / SAMPLE_RATE,
                    synth_seconds=time.perf_counter() - start)

    async def speak(self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> Clip:
        if self.engine is None:
            raise TTSError("No neural voice engine is installed.",
                           hint="pip install kokoro-onnx soundfile, then restart AETHER.")
        clean = prepare_for_speech(text)[:MAX_CHARS]
        if not clean:
            raise TTSError("There's nothing to say in that text.")
        voice = voice if voice in VOICES else DEFAULT_VOICE
        speed = min(MAX_SPEED, max(MIN_SPEED, float(speed)))
        return await asyncio.to_thread(self._locked, self._synth, clean, voice, speed)


if __name__ == "__main__":  # python tts.py "Hello there"  → writes hello.wav and prints timings
    import sys
    from aether.config import get_settings

    service = TTSService(get_settings().data_dir / "tts")
    print("engine:", service.engine_name)
    clip = asyncio.run(service.speak(" ".join(sys.argv[1:]) or "Hello, I'm AETHER. Your voice is ready."))
    Path("hello.wav").write_bytes(clip.wav)
    print(f"wrote hello.wav: {clip.seconds:.2f}s of audio in {clip.synth_seconds:.2f}s")
