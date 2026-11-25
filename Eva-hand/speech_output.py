"""Offline TTS: Piper lub eSpeak NG -> tymczasowy WAV -> ALSA."""
from __future__ import annotations

import http.client
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

DEFAULT_PIPER_PYTHON = "~/ewa-tts/.venv/bin/python"
DEFAULT_PIPER_MODEL = "~/ewa-tts/pl_PL-gosia-medium.onnx"
DEFAULT_TTS_SERVER = "http://10.77.77.1:8001"
MAX_WAV_BYTES = 24 * 1024 * 1024


class SpeechError(Exception):
    pass


def piper_paths(python, model):
    # resolve() interpretera zgubiłoby .venv (symlink do systemowego Pythona).
    executable = Path(python).expanduser().absolute()
    voice = Path(model).expanduser().absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SpeechError(f"Brak interpretera Pipera: {executable}")
    for path in (voice, Path(str(voice) + ".json")):
        if not path.is_file():
            raise SpeechError(f"Brak pliku głosu Pipera: {path}")
    return executable, voice


def check_dependencies(engine="espeak", piper_python=DEFAULT_PIPER_PYTHON,
                       piper_model=DEFAULT_PIPER_MODEL, fallback=True) -> None:
    if engine not in ("espeak", "piper", "piper-remote"):
        raise SpeechError("Silnik mowy musi być espeak, piper lub piper-remote.")
    if not shutil.which("aplay"):
        raise SpeechError("Brakuje aplay. Uruchom: sudo apt install alsa-utils")
    if engine == "espeak":
        if not shutil.which("espeak-ng"):
            raise SpeechError("Brakuje espeak-ng. Uruchom: sudo apt install espeak-ng")
    elif engine == "piper":
        try:
            piper_paths(piper_python, piper_model)
        except SpeechError:
            if not fallback or not shutil.which("espeak-ng"):
                raise


def tts_url(server: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(server)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment or
                parsed.username or parsed.password or
                any(c.isspace() for c in server)):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except ValueError as exc:
        raise SpeechError("--tts-server musi być adresem http(s)://host:port bez ścieżki.") from exc
    return server.rstrip("/")


def remote_piper(text: str, output: Path, server: str, length_scale: float) -> None:
    body = json.dumps({"text": text, "length_scale": length_scale}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(tts_url(server) + "/tts", data=body,
                                     headers={"Content-Type": "application/json"})
    # Połączenie USB nie powinno przechodzić przez proxy ze środowiska.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=120) as response:
            if response.status != 200 or response.headers.get_content_type() != "audio/wav":
                raise SpeechError("Serwer Pipera nie zwrócił pliku WAV.")
            with output.open("wb") as target:
                size = 0
                while chunk := response.read(65536):
                    size += len(chunk)
                    if size > MAX_WAV_BYTES:
                        raise SpeechError("Odpowiedź Pipera przekroczyła limit 24 MiB.")
                    target.write(chunk)
        # Sprawdź także rzeczywistą liczbę próbek, nie tylko nagłówek WAV.
        with wave.open(str(output), "rb") as audio:
            frames = audio.getnframes()
            expected = frames * audio.getnchannels() * audio.getsampwidth()
            if expected > MAX_WAV_BYTES or len(audio.readframes(frames)) != expected:
                raise SpeechError("Niepełny lub zbyt duży WAV z Pi 5.")
        wav_duration(output)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise SpeechError(f"Piper na Pi 5: HTTP {status}; sprawdź terminal serwera.") from exc
    except (urllib.error.URLError, OSError, TimeoutError, EOFError, wave.Error, http.client.HTTPException) as exc:
        raise SpeechError(f"Piper na Pi 5: {exc}") from exc


def _proc_values(path: Path) -> dict[str, int]:
    try:
        return {parts[0].rstrip(":"): int(parts[1])
                for line in path.read_text().splitlines()
                if len(parts := line.split()) >= 3 and parts[1].isdigit() and parts[2] == "kB"}
    except (OSError, ValueError):
        return {}


class MemorySamples:
    """Próbkowanie /proc co 0.1 s; to przybliżenie, nie dokładny szczyt RSS."""
    def __init__(self, pid: int, label: str):
        self.pid, self.label = pid, label
        self.rss = 0
        self.available = None
        self.swap_free = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _sample(self):
        self.rss = max(self.rss, _proc_values(Path(f"/proc/{self.pid}/status")).get("VmRSS", 0))
        values = _proc_values(Path("/proc/meminfo"))
        for field, key in (("available", "MemAvailable"), ("swap_free", "SwapFree")):
            if key in values:
                previous = getattr(self, field)
                setattr(self, field, values[key] if previous is None else min(previous, values[key]))

    def _watch(self):
        while not self.stop.is_set():
            self._sample()
            self.stop.wait(0.1)

    def finish(self):
        self.stop.set()
        self.thread.join()
        if self.available is not None and self.swap_free is not None:
            print(f"[pamięć {self.label}: próbki RSS maks. {self.rss / 1024:.0f} MiB; "
                  f"RAM dostępny min. {self.available / 1024:.0f} MiB; "
                  f"swap wolny min. {self.swap_free / 1024:.0f} MiB]", flush=True)


def run_audio(command: list[str], timeout: float, data: bytes | None = None,
              *, capture=False, diagnostics=False, label="audio") -> bytes:
    """Zakończ i odbierz proces także po Ctrl+C/timeout, zanim ruszy kolejny."""
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
    except OSError as exc:
        raise SpeechError(f"Nie można uruchomić {command[0]}: {exc}") from exc
    monitor = MemorySamples(process.pid, label) if diagnostics else None
    try:
        if monitor:
            monitor.thread.start()
        try:
            output, error = process.communicate(input=data, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SpeechError(f"{label}: przekroczono limit {timeout:g} s.") from exc
        if process.returncode:
            detail = (error or b"").decode("utf-8", errors="replace").strip()
            raise SpeechError(f"{label}: {detail or process.returncode}")
        return output or b""
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        if monitor:
            monitor.finish()


def wav_duration(output: Path) -> float:
    try:
        with wave.open(str(output), "rb") as audio:
            rate = audio.getframerate()
            duration = audio.getnframes() / rate if rate > 0 else 0
            if not 0 < duration <= 240:
                raise SpeechError("Nieprawidłowa długość WAV wygenerowanego przez syntezator.")
            return duration
    except (OSError, EOFError, wave.Error) as exc:
        raise SpeechError(f"Syntezator nie utworzył poprawnego WAV: {exc}") from exc


def speak(text: str, device: str = "default", voice: str = "pl", rate: int = 160,
          *, engine="espeak", piper_python=DEFAULT_PIPER_PYTHON,
          piper_model=DEFAULT_PIPER_MODEL, length_scale=1.0, fallback=True,
          diagnostics=False, tts_server=DEFAULT_TTS_SERVER) -> tuple[float, float]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500:
        raise SpeechError("Tekst do odczytania musi mieć 1–1500 znaków.")
    if not 80 <= rate <= 300:
        raise SpeechError("Tempo eSpeak musi być w zakresie 80–300.")
    if not math.isfinite(length_scale) or not 0.5 <= length_scale <= 2:
        raise SpeechError("Tempo Pipera (length_scale) musi być w zakresie 0.5–2.")
    check_dependencies(engine, piper_python, piper_model, fallback)
    with tempfile.TemporaryDirectory(prefix="eva-speech-") as temporary:
        output = Path(temporary) / "answer.wav"
        started = time.monotonic()
        selected = engine
        if engine in ("piper", "piper-remote"):
            try:
                if engine == "piper-remote":
                    remote_piper(text.strip(), output, tts_server, length_scale)
                else:
                    executable, model = piper_paths(piper_python, piper_model)
                    # Piper 1.8: tekst z stdin, jeden docelowy WAV; bez powłoki.
                    run_audio([str(executable), "-m", "piper", "-m", str(model), "-f", str(output),
                               "--length-scale", str(length_scale)], 120,
                              text.strip().encode("utf-8"), diagnostics=diagnostics, label="Piper")
                duration = wav_duration(output)
            except SpeechError as exc:
                if not fallback:
                    raise
                print(f"[Piper niedostępny: {exc}. Używam awaryjnie eSpeak NG.]",
                      file=sys.stderr, flush=True)
                output.unlink(missing_ok=True)
                selected = "espeak"
        if selected == "espeak":
            check_dependencies("espeak")
            run_audio(["espeak-ng", "-b", "1", "-v", voice, "-s", str(rate), "-a", "100",
                       "-w", str(output), "--stdin"], 30, text.strip().encode("utf-8"),
                      diagnostics=diagnostics, label="eSpeak")
            duration = wav_duration(output)
        synthesis = time.monotonic() - started
        label = {"piper": "Piper", "piper-remote": "Piper na Pi 5", "espeak": "eSpeak NG"}[selected]
        print(f"[głos: {label}]", flush=True)
        started = time.monotonic()
        # Błąd odtwarzania NIE powoduje ponownej syntezy/odczytu.
        run_audio(["aplay", "-q", "-D", device, str(output)], duration + 15)
        return synthesis, time.monotonic() - started
