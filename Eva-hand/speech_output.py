"""Mowa offline na Pi 3: eSpeak NG -> tymczasowy WAV -> wybrane wyjście ALSA."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path


class SpeechError(Exception):
    pass


def check_dependencies() -> None:
    missing = [name for name in ("espeak-ng", "aplay") if not shutil.which(name)]
    if missing:
        raise SpeechError("Brakuje " + ", ".join(missing) +
                          ". Uruchom: sudo apt install espeak-ng alsa-utils")


def run_audio(command: list[str], timeout: float, data: bytes | None = None) -> None:
    """Zawsze zakończ proces i zwolnij audio, także po Ctrl+C i timeout."""
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as exc:
        raise SpeechError(f"Nie można uruchomić {command[0]}: {exc}") from exc
    try:
        try:
            _, error = process.communicate(input=data, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SpeechError(f"{command[0]} przekroczył limit czasu.") from exc
        if process.returncode:
            detail = (error or b"").decode("utf-8", errors="replace").strip()
            raise SpeechError(f"{command[0]}: {detail or process.returncode}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stderr):
            if stream is not None:
                stream.close()


def speak(text: str, device: str = "default", voice: str = "pl", rate: int = 160) -> tuple[float, float]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500:
        raise SpeechError("Tekst do odczytania musi mieć 1–1500 znaków.")
    if not 80 <= rate <= 300:
        raise SpeechError("Tempo mowy musi być w zakresie 80–300.")
    check_dependencies()
    with tempfile.TemporaryDirectory(prefix="eva-speech-") as temporary:
        output = Path(temporary) / "answer.wav"
        started = time.monotonic()
        # Tekst jest danymi na stdin, nigdy poleceniem powłoki ani argumentem programu.
        run_audio(["espeak-ng", "-b", "1", "-v", voice, "-s", str(rate), "-a", "100",
                   "-w", str(output), "--stdin"], 30, text.strip().encode("utf-8"))
        synthesis = time.monotonic() - started
        try:
            with wave.open(str(output), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                if not 0 < duration <= 240:
                    raise SpeechError("Nieprawidłowa długość WAV wygenerowanego przez syntezator.")
        except (OSError, EOFError, wave.Error) as exc:
            raise SpeechError(f"Syntezator nie utworzył poprawnego WAV: {exc}") from exc
        started = time.monotonic()
        run_audio(["aplay", "-q", "-D", device, str(output)], duration + 15)
        return synthesis, time.monotonic() - started
