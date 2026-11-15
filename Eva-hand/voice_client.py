#!/usr/bin/env python3
"""Pi 3: Enter -> mikrofon/Vosk -> API Pi 5 -> tekst i opcjonalnie głośnik."""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from speech_output import (SpeechError, check_dependencies, speak, run_audio,
                           DEFAULT_PIPER_PYTHON, DEFAULT_PIPER_MODEL)

ROOT = Path(__file__).resolve().parent
RATE = 16000
DEFAULT_MODEL = ROOT / "models" / "vosk-model-small-pl-0.22"


class ClientError(Exception):
    """Błąd, który można wyświetlić bez stosu wywołań."""


def request_json(url: str, payload: dict | None, timeout: float) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="GET" if payload is None else "POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    # Ruch do lokalnego Pi ma iść bezpośrednio, także przy ustawionym HTTP_PROXY.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
        raise ClientError(f"API zwróciło HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ClientError(
            f"Brak odpowiedzi z {url}: {exc}. Sprawdź USB i serwer na Pi 5. "
            "Program nie ponawia automatycznie żądania /chat."
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise ClientError("API nie zwróciło poprawnego JSON.") from exc
    if not isinstance(result, dict):
        raise ClientError("API zwróciło JSON, ale nie obiekt.")
    return result


def check_server(server: str) -> dict:
    result = request_json(server + "/health", None, 10)
    if result.get("status") != "ok":
        raise ClientError("Pi 5 nie jest gotowe: " + json.dumps(result, ensure_ascii=False))
    return result


def ask_brain(server: str, text: str, session: str, timeout: float) -> dict:
    text = text.strip()
    if not 1 <= len(text) <= 600:
        raise ClientError("Wypowiedź musi mieć 1–600 znaków. Powiedz krócej; nic nie wysłano.")
    result = request_json(server + "/chat", {"session_id": session, "text": text}, timeout)
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ClientError("Odpowiedź API nie zawiera niepustego pola text.")
    return result


def record_wav(destination: Path, device: str, seconds: int) -> None:
    command = [
        "arecord", "-q", "-D", device, "-t", "wav", "-f", "S16_LE",
        "-r", str(RATE), "-c", "1", "-d", str(seconds), str(destination),
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ClientError(f"Nie można uruchomić arecord: {exc}") from exc
    try:
        try:
            _, stderr = process.communicate(timeout=seconds + 10)
        except subprocess.TimeoutExpired as exc:
            raise ClientError("Nagrywanie przekroczyło limit czasu. Sprawdź urządzenie ALSA.") from exc
        if process.returncode:
            detail = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise ClientError(
                f"Nagrywanie nie powiodło się: {detail or process.returncode}. "
                "Sprawdź --list-devices i --device."
            )
    finally:
        # Także Ctrl+C, timeout i wyjątek zwalniają urządzenie audio.
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stderr is not None:
            process.stderr.close()


def transcribe_wav(path: Path, model, recognizer_factory) -> str:
    parts = []
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                    audio.getcomptype()) != (1, 2, RATE, "NONE"):
                raise ClientError("Nagranie musi być WAV PCM: mono, 16 bit, 16000 Hz.")
            recognizer = recognizer_factory(model, RATE)
            while data := audio.readframes(4000):
                if recognizer.AcceptWaveform(data):
                    parts.append(json.loads(recognizer.Result()).get("text", ""))
            # Zachowaj również końcówkę wypowiedzi, bez ciszy na końcu nagrania.
            parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
    except (OSError, EOFError, wave.Error) as exc:
        raise ClientError(f"Nie można odczytać WAV: {exc}") from exc
    return " ".join(part.strip() for part in parts if part.strip())


def load_vosk(path: Path):
    if not (path / "am" / "final.mdl").is_file():
        raise ClientError(
            f"Nie ma modelu Vosk w {path}. Uruchom bash setup_pi3.sh "
            "lub wskaż istniejący model przez --model /pełna/ścieżka."
        )
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except (ImportError, OSError) as exc:
        raise ClientError(
            "Nie można załadować Vosk. Uruchom bash setup_pi3.sh, "
            "a program przez .venv/bin/python. Szczegóły: " + str(exc)
        ) from exc
    SetLogLevel(-1)
    print("Ładuję model Vosk — tylko raz na uruchomienie...", flush=True)
    try:
        model = Model(str(path))
    except Exception as exc:
        raise ClientError(f"Nie można otworzyć modelu Vosk: {exc}") from exc
    return model, KaldiRecognizer


def transcribe_isolated(path: Path, model_path: Path, diagnostics=False) -> str:
    """Proces Vosk kończy się i oddaje RAM przed zapytaniem HTTP i TTS."""
    try:
        raw = run_audio([sys.executable, str(ROOT / "stt_worker.py"),
                         "--model", str(model_path), "--wav", str(path)],
                        180, capture=True, diagnostics=diagnostics, label="Vosk")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("brak pola text")
        return result["text"]
    except (SpeechError, ValueError, UnicodeError) as exc:
        raise ClientError(f"Nie udało się rozpoznać mowy: {exc}") from exc


def speech_options(args):
    return dict(engine=args.tts_engine, piper_python=args.piper_python,
                piper_model=args.piper_model, length_scale=args.piper_length_scale,
                fallback=args.tts_fallback, diagnostics=args.audio_diagnostics)


def say(text, args):
    synthesis, playback = speak(text, args.output_device, args.voice, args.speech_rate,
                               **speech_options(args))
    print(f"[synteza: {synthesis:.1f} s; odtwarzanie: {playback:.1f} s]", flush=True)


def handle_text(text: str, args) -> bool:
    text = text.strip()
    if not text:
        print("Nie rozpoznano mowy. Spróbuj ponownie.", flush=True)
        return False
    print("Ty:", text, flush=True)
    if args.stt_only:
        return True
    print("Czekam na odpowiedź Pi 5...", flush=True)
    start = time.monotonic()
    result = ask_brain(args.server, text, args.session, args.timeout)
    print("E.W.A.:", result["text"], flush=True)
    if result.get("memory") not in (None, "none"):
        print("[pamięć:", str(result["memory"]) + "]", flush=True)
    print(f"[czas oczekiwania na API: {time.monotonic() - start:.1f} s]", flush=True)
    if args.speak:
        try:
            say(result["text"], args)
        except SpeechError as exc:
            print(f"Błąd głośnika: {exc}. Odpowiedź tekstowa jest powyżej; pytania nie wysyłam ponownie.",
                  file=sys.stderr, flush=True)
            return False
    return True


def parse_args(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", type=Path)
    known, _ = config_parser.parse_known_args(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="lokalny JSON z ustawieniami audio")
    parser.add_argument("--server", default="http://10.77.77.1:8000",
                        help="adres API Pi 5 bez /chat")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL,
                        help="katalog rozpakowanego modelu Vosk")
    parser.add_argument("--device", default="default", help="urządzenie ALSA, np. plughw:CARD=...,DEV=0")
    parser.add_argument("--seconds", type=int, default=8, help="czas nagrania: 1–30 s (domyślnie 8)")
    parser.add_argument("--session", default="ewa-voice", help="stały identyfikator rozmowy")
    parser.add_argument("--timeout", type=float, default=390, help="limit oczekiwania na API w sekundach")
    parser.add_argument("--output-device", default="default", help="wyjście ALSA dla głośnika")
    parser.add_argument("--voice", default="pl", help="głos eSpeak NG, domyślnie pl")
    parser.add_argument("--speech-rate", type=int, default=160, help="tempo mowy: 80–300")
    parser.add_argument("--tts-engine", choices=("espeak", "piper"), default="espeak")
    parser.add_argument("--piper-python", default=DEFAULT_PIPER_PYTHON)
    parser.add_argument("--piper-model", default=DEFAULT_PIPER_MODEL)
    parser.add_argument("--piper-length-scale", type=float, default=1.0,
                        help="tempo Pipera: 0.5–2; większa wartość = wolniej")
    parser.add_argument("--tts-fallback", action=argparse.BooleanOptionalAction, default=True,
                        help="po błędzie syntezy Piper użyj eSpeak NG")
    parser.add_argument("--stt-mode", choices=("auto", "isolated", "persistent"), default="auto",
                        help="auto: oddzielny proces Vosk dla mowy Piper; oszczędza RAM")
    parser.add_argument("--audio-diagnostics", action=argparse.BooleanOptionalAction, default=False,
                        help="próbkuj RSS procesów audio oraz dostępny RAM i wolny swap")
    speech_mode = parser.add_mutually_exclusive_group()
    speech_mode.add_argument("--speak", dest="speak", action="store_true", help="odczytuj odpowiedzi przez głośnik")
    speech_mode.add_argument("--no-speak", dest="speak", action="store_false", help="tylko odpowiedzi tekstowe")
    parser.set_defaults(speak=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="sprawdź API bez Vosk i mikrofonu")
    modes.add_argument("--list-devices", action="store_true", help="pokaż urządzenia ALSA")
    modes.add_argument("--list-outputs", action="store_true", help="pokaż wyjścia ALSA dla głośnika")
    modes.add_argument("--tts-test", action="store_true", help="test głośnika bez AI i mikrofonu")
    modes.add_argument("--text", help="jedno pytanie tekstowe bez Vosk i mikrofonu")
    modes.add_argument("--wav", type=Path, help="rozpoznaj jeden istniejący plik WAV zamiast nagrywać")
    parser.add_argument("--stt-only", action="store_true", help="tylko rozpoznawanie mowy; bez API")
    parser.add_argument("--once", action="store_true", help="zakończ po jednej próbie nagrania")
    config_path = known.config.expanduser() if known.config else ROOT / "audio.local.json"
    if known.config or config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            allowed = {"device": str, "output_device": str, "voice": str,
                       "seconds": int, "speech_rate": int, "speak": bool,
                       "tts_engine": str, "piper_python": str, "piper_model": str,
                       "piper_length_scale": (int, float), "tts_fallback": bool,
                       "stt_mode": str, "audio_diagnostics": bool}
            if not isinstance(config, dict) or set(config) - set(allowed):
                raise ValueError("dozwolone klucze: " + ", ".join(allowed))
            for key, value in config.items():
                types = allowed[key] if isinstance(allowed[key], tuple) else (allowed[key],)
                if type(value) not in types:
                    raise ValueError(f"niepoprawny typ pola {key}")
            parser.set_defaults(**config)
        except (OSError, ValueError, UnicodeError) as exc:
            parser.error(f"Nie można wczytać {config_path}: {exc}")
    args = parser.parse_args(argv)
    args.server = args.server.rstrip("/")
    parsed = urllib.parse.urlsplit(args.server)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        parser.error("--server musi być adresem http(s)://host:port, bez /chat ani /health")
    if not 1 <= args.seconds <= 30:
        parser.error("--seconds musi być liczbą od 1 do 30")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.session):
        parser.error("--session: 1–32 znaki, litery ASCII, cyfry, _ lub -")
    if not 1 <= args.timeout <= 3600:
        parser.error("--timeout musi być w zakresie 1–3600 s")
    if not 80 <= args.speech_rate <= 300:
        parser.error("--speech-rate musi być w zakresie 80–300")
    if args.tts_engine not in ("espeak", "piper") or args.stt_mode not in ("auto", "isolated", "persistent"):
        parser.error("Niepoprawny tts_engine lub stt_mode w konfiguracji")
    if not math.isfinite(args.piper_length_scale) or not 0.5 <= args.piper_length_scale <= 2:
        parser.error("--piper-length-scale musi być w zakresie 0.5–2")
    for value in (args.piper_python, args.piper_model):
        if not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
            parser.error("Niepoprawna ścieżka Pipera")
    for name in ("device", "output_device", "voice"):
        value = getattr(args, name)
        if not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
            parser.error(f"Niepoprawna wartość {name}")
    args.model = args.model.expanduser()
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.list_outputs:
            if not shutil.which("aplay"):
                raise ClientError("Brakuje aplay. Zainstaluj pakiet alsa-utils.")
            first = subprocess.run(["aplay", "-l"], check=False).returncode
            second = subprocess.run(["aplay", "-L"], check=False).returncode
            return int(first != 0 or second != 0)
        if args.tts_test:
            say("Cześć Bartłomieju. Tu Ewa. Test głośnika działa.", args)
            return 0
        if args.list_devices:
            if not shutil.which("arecord"):
                raise ClientError("Brakuje arecord. Zainstaluj pakiet alsa-utils.")
            first = subprocess.run(["arecord", "-l"], check=False).returncode
            second = subprocess.run(["arecord", "-L"], check=False).returncode
            return int(first != 0 or second != 0)
        if args.check:
            print(json.dumps(check_server(args.server), ensure_ascii=False, indent=2))
            return 0
        if args.speak and not args.stt_only:
            check_dependencies(args.tts_engine, args.piper_python, args.piper_model, args.tts_fallback)
        if args.text is not None:
            if not args.text.strip():
                raise ClientError("Pytanie --text nie może być puste.")
            return 0 if handle_text(args.text, args) else 1
        if not args.stt_only:
            info = check_server(args.server)
            print(f"Pi 5 gotowe. Model: {info.get('model', '?')}", flush=True)
        if args.wav is None and not shutil.which("arecord"):
            raise ClientError("Brakuje arecord. Zainstaluj pakiet alsa-utils.")
        isolated = args.stt_mode == "isolated" or (
            args.stt_mode == "auto" and args.tts_engine == "piper" and args.speak and not args.stt_only)
        if isolated:
            model, recognizer_factory = None, None
            print("Tryb oszczędny: Vosk ładuje się dla każdego nagrania i kończy pracę przed Piperem.", flush=True)
        else:
            model, recognizer_factory = load_vosk(args.model)

        def recognize(path):
            if isolated:
                return transcribe_isolated(path, args.model, args.audio_diagnostics)
            return transcribe_wav(path, model, recognizer_factory)

        if args.wav is not None:
            return 0 if handle_text(recognize(args.wav.expanduser()), args) else 1
        print(f"Nagrywanie: {args.seconds} s, urządzenie: {args.device}. "
              f"Głośnik: {'włączony' if args.speak and not args.stt_only else 'wyłączony'}.")
        while True:
            try:
                command = input("Enter = nagranie; q + Enter = koniec: ").strip().lower()
            except EOFError:
                return 0
            if command in {"q", "quit", "exit", "/exit"}:
                return 0
            if command:
                print("Naciśnij sam Enter, aby nagrać wypowiedź.")
                continue
            try:
                # Plik znika przed wywołaniem modelu na Pi 5.
                with tempfile.TemporaryDirectory(prefix="eva-voice-") as temporary:
                    wav_path = Path(temporary) / "question.wav"
                    print(f"Mów teraz — nagrywam przez {args.seconds} s...", flush=True)
                    record_wav(wav_path, args.device, args.seconds)
                    print("Rozpoznaję mowę...", flush=True)
                    recognition_started = time.monotonic()
                    text = recognize(wav_path)
                    print(f"[rozpoznawanie: {time.monotonic() - recognition_started:.1f} s]", flush=True)
                ok = handle_text(text, args)
                if args.once:
                    return 0 if ok else 1
            except ClientError as exc:
                print("Błąd:", exc, file=sys.stderr, flush=True)
                if args.once:
                    return 1
    except KeyboardInterrupt:
        print("\nZakończono.", flush=True)
        return 130
    except (ClientError, SpeechError) as exc:
        print("Błąd:", exc, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

