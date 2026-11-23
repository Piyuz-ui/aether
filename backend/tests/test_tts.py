"""Neural voice: speech-friendly text, sentence splitting, engine status and synthesis."""
from __future__ import annotations

import io

import numpy as np
import pytest

import tts
from tts import TTSService, prepare_for_speech, split_sentences


def test_prepare_for_speech_reads_naturally():
    md = ("## Plan 📅\n**Bold** and *italic* with `code`. See [the docs](https://x.org/a?b=1) or https://y.com.\n"
          "- Mon → DBMS\n```python\nprint(1)\n```\n> quoted")
    out = prepare_for_speech(md)
    assert "**" not in out and "`" not in out and "#" not in out and "📅" not in out
    assert "http" not in out and "x.org" not in out and "the docs." in out
    assert "I've put the code block on screen." in out and "print(1)" not in out
    assert "Mon to DBMS" in out and "quoted" in out


@pytest.mark.parametrize("text, expected", [
    ("Dr. Smith said Python 3.15 is out. It's fast!", ["Dr. Smith said Python 3.15 is out.", "It's fast!"]),
    ("Use e.g. lists, i.e. arrays. Then sort them.", ["Use e.g. lists, i.e. arrays.", "Then sort them."]),
    ("Sure. Here is the full explanation of the topic.", ["Sure. Here is the full explanation of the topic."]),
])
def test_split_sentences_respects_abbreviations_and_decimals(text, expected):
    assert split_sentences(text) == expected


def test_long_sentences_are_broken_at_commas():
    parts = split_sentences("word, " * 120 + "end.", max_chars=100)
    assert len(parts) > 3 and all(len(p) <= 101 for p in parts)


class FakeEngine:
    name = "kokoro-fake"

    def __init__(self):
        self.calls = []
        self.loaded = False

    def ready(self):
        return self.loaded

    def load(self):
        self.loaded = True

    def synth(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        return np.sin(np.linspace(0, 400, int(tts.SAMPLE_RATE * 0.5))).astype(np.float32) * 0.3


async def test_service_synthesises_wav_and_clamps_inputs(tmp_path):
    svc = TTSService(tmp_path)
    svc.engine = FakeEngine()
    assert svc.status()["state"] == "idle"
    clip = await svc.speak("**Hello** there, it is lovely to meet you. How are you feeling about exams?",
                           voice="nope", speed=9)
    assert clip.wav[:4] == b"RIFF" and clip.seconds > 0.9  # two sentences + a short breath between
    assert {c[1] for c in svc.engine.calls} == {"af_heart"} and {c[2] for c in svc.engine.calls} == {1.25}
    assert svc.status()["state"] == "ready"
    import soundfile as sf
    data, sr = sf.read(io.BytesIO(clip.wav))
    assert sr == 24000 and len(data) > 0


async def test_no_engine_reports_fallback(tmp_path):
    svc = TTSService(tmp_path)
    svc.engine = None
    st = svc.status()
    assert st["engine"] == "fallback" and st["state"] == "unavailable" and "kokoro-onnx" in st["hint"]
    with pytest.raises(tts.TTSError):
        await svc.speak("hi")


def test_voice_presets():
    ids = [v["id"] for v in TTSService(tts.Path("/nonexistent")).status()["voices"]]
    assert ids == ["af_heart", "af_bella", "bm_george", "bf_emma"]
