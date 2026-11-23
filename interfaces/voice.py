"""Voice interface: push-to-talk, offline speech-to-text (faster-whisper),
and offline text-to-speech (macOS `say`, or pyttsx3 elsewhere).

Install the extras first:  pip install -r requirements-voice.txt
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading

from agent import Agent, Settings

SAMPLE_RATE = 16000
WHISPER_MODEL = os.getenv("AGENT_WHISPER_MODEL", "base.en")


def _clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " (I've put the code on screen.) ", text, flags=re.DOTALL)
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # markdown links → their text
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self) -> None:
        self.use_say = platform.system() == "Darwin" and shutil.which("say")
        self.engine = None
        if not self.use_say:
            import pyttsx3
            self.engine = pyttsx3.init()

    def say(self, text: str) -> None:
        text = _clean_for_speech(text)
        if not text:
            return
        if self.use_say:
            subprocess.run(["say", text])
        else:
            self.engine.say(text)
            self.engine.runAndWait()


class Listener:
    def __init__(self) -> None:
        from faster_whisper import WhisperModel
        print(f"Loading speech model '{WHISPER_MODEL}' (downloads once, then works offline)…")
        self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def record(self) -> "numpy.ndarray":  # noqa: F821
        """Record from the mic until the user presses Enter again."""
        import numpy as np
        import sounddevice as sd

        chunks: list = []
        stop = threading.Event()

        def callback(indata, frames, time, status):  # noqa: ARG001
            chunks.append(indata.copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            callback=callback):
            threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
            stop.wait()
        return np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")

    def transcribe(self, audio) -> str:
        if audio.size < SAMPLE_RATE // 2:  # under half a second
            return ""
        segments, _ = self.model.transcribe(audio, language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def main() -> None:
    try:
        listener = Listener()
        speaker = Speaker()
    except ImportError as e:
        print(f"Voice mode needs extra packages ({e.name}).\n"
              "Install them with:  pip install -r requirements-voice.txt")
        return

    def confirm(name: str, args: dict) -> bool:
        speaker.say(f"I'd like to run {name.replace('_', ' ')}. Should I go ahead?")
        print(f"⚠️  Allow {name} {args}? [y/N] ", end="", flush=True)
        return input().strip().lower() in {"y", "yes"}

    def on_event(kind: str, payload: dict) -> None:
        if kind == "tool_call":
            print(f"  🔧 {payload['name']}")

    agent = Agent(Settings(), confirm=confirm, on_event=on_event)
    name = agent.settings.agent_name
    greeting = f"Hi, I'm {name}. Press Enter, talk, then press Enter again."
    print(f"\n🎙️  {greeting}  Say 'goodbye' or press Ctrl+C to quit.")
    speaker.say(f"Hi, I'm {name}. How can I help?")

    while True:
        try:
            input("\n[Enter] to talk… ")
            print("🔴 Listening — press Enter when you're done.")
            text = listener.transcribe(listener.record())
        except (KeyboardInterrupt, EOFError):
            break
        if not text:
            print("(didn't catch that)")
            continue
        print(f"you › {text}")
        if re.search(r"\b(goodbye|bye|exit|stop listening)\b", text.lower()):
            speaker.say("Goodbye!")
            break
        print("thinking…")
        reply = agent.chat(text)
        print(f"\n{name} › {reply}")
        speaker.say(reply)


if __name__ == "__main__":
    main()
