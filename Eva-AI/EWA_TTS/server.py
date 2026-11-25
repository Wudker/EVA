#!/usr/bin/env python3
"""Pi 5: jeden załadowany Piper, tekst przez USB -> WAV odtwarzany na Pi 3."""
from __future__ import annotations

import argparse
import json
import logging
import math
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 16384
MAX_WAV = 24 * 1024 * 1024


def memory_info():
    """Bieżące wartości, nie szczyt użycia i nie dowód aktywnego swapowania."""
    result = {}
    for filename, field, target in (("/proc/meminfo", "MemAvailable:", "available_mib"),
                                    ("/proc/self/status", "VmRSS:", "tts_rss_mib")):
        try:
            for line in Path(filename).read_text().splitlines():
                if line.startswith(field):
                    result[target] = round(int(line.split()[1]) / 1024, 1)
        except (OSError, ValueError, IndexError):
            pass
    return result


class PiperService:
    def __init__(self, model: Path):
        # Import dopiero przy starcie: --help i testy HTTP nie wymagają Pipera.
        from piper import PiperVoice, SynthesisConfig
        self.voice = PiperVoice.load(str(model))
        self.config_factory = SynthesisConfig
        self.model = model.name
        self.lock = threading.Lock()
        # Rozgrzej również fonemizator/ONNX, zanim /health zgłosi gotowość.
        self.render("Cześć. Tu Ewa.", 1.0)

    def render(self, text: str, length_scale: float) -> bytes:
        with tempfile.TemporaryFile() as output:
            with wave.open(output, "wb") as audio:
                self.voice.synthesize_wav(text, audio,
                                          syn_config=self.config_factory(length_scale=length_scale))
            size = output.tell()
            if not 44 < size <= MAX_WAV:
                raise ValueError("Nieprawidłowy rozmiar WAV")
            output.seek(0)
            with wave.open(output, "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                if not 0 < duration <= 240:
                    raise ValueError("Nieprawidłowa długość WAV")
            output.seek(0)
            return output.read(MAX_WAV + 1)


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

        def respond(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(body)
            except (OSError, TimeoutError):
                logging.warning("Klient przerwał odbieranie odpowiedzi TTS")

        def do_GET(self):
            if self.path != "/health":
                self.respond(404, {"error": "Brak takiej ścieżki"})
                return
            self.respond(200, {"status": "ready", "engine": "piper", "model": service.model,
                               "busy": service.lock.locked(), **memory_info()})

        def do_POST(self):
            if self.path != "/tts":
                self.respond(404, {"error": "Brak takiej ścieżki"})
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Transfer-Encoding nie jest obsługiwane")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Wymagany Content-Type: application/json")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ValueError("Wymagany Content-Length 1–16384")
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("Niepełne żądanie")
                data = json.loads(body)
                if not isinstance(data, dict) or set(data) - {"text", "length_scale"}:
                    raise ValueError("Dozwolone pola: text, length_scale")
                text = data.get("text")
                scale = data.get("length_scale", 1.0)
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500:
                    raise ValueError("text: wymagane 1–1500 znaków")
                if type(scale) not in (int, float) or not math.isfinite(scale) or not 0.5 <= scale <= 2:
                    raise ValueError("length_scale: wymagane 0.5–2")
            except (ValueError, UnicodeError, OSError) as exc:
                self.respond(400, {"error": str(exc)})
                return
            if not service.lock.acquire(blocking=False):
                self.respond(503, {"error": "Piper zajęty; spróbuj po zakończeniu syntezy"})
                return
            try:
                started = time.monotonic()
                audio = service.render(text.strip(), scale)
                logging.info("Synteza %.2f s; %d znaków; pamięć %s",
                             time.monotonic() - started, len(text.strip()), memory_info())
            except Exception:
                logging.exception("Błąd syntezy Pipera")
                self.respond(500, {"error": "Błąd syntezy; sprawdź terminal serwera"})
                return
            finally:
                service.lock.release()
            self.respond(200, audio, "audio/wav")
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="10.77.77.1", help="adres USB Pi 5")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--model", type=Path, default=Path("~/ewa-tts/pl_PL-gosia-medium.onnx"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port musi być w zakresie 1–65535")
    model = args.model.expanduser()
    for path in (model, Path(str(model) + ".json")):
        if not path.is_file():
            parser.error(f"Brak {path}; uruchom bash instalation/install_piper.sh na Pi 5")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.info("Ładuję i rozgrzewam Piper — raz na uruchomienie serwera...")
    try:
        service = PiperService(model)
        with ThreadingHTTPServer((args.host, args.port), make_handler(service)) as server:
            logging.info("Piper gotowy: http://%s:%d; pamięć %s", args.host, args.port, memory_info())
            server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Zatrzymano serwer Pipera")
        return 0
    except Exception:
        logging.exception("Nie udało się uruchomić serwera Pipera")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
