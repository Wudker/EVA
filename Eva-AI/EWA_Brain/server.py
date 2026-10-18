"""E.W.A. Brain API: tylko USB (domyślnie 10.77.77.1:8000), bez MQTT/STT/TTS."""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from modules.brain import Brain, OllamaError

HOST = os.getenv("EVA_USB_IP", "10.77.77.1")
PORT = int(os.getenv("EVA_PORT", "8000"))
brain = Brain()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self._reply(404, {"error": "Not found"})
        info = brain.health()
        self._reply(200 if info["status"] == "ok" else 503, info)

    def do_POST(self):
        if self.path != "/chat":
            return self._reply(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096:
                return self._reply(413, {"error": "Przekroczony limit żądania"})
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                return self._reply(400, {"error": "Oczekiwano obiektu JSON"})
            result = brain.handle(data.get("text"), data.get("session_id", "ewa-main"))
            self._reply(200, result)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._reply(400, {"error": str(exc)})
        except OllamaError as exc:
            logging.warning("Nie udało się uzyskać odpowiedzi modelu: %s", exc)
            self._reply(502, {"error": "Błąd lokalnego modelu. Sprawdź journalctl -u ewa-brain."})
        except Exception:
            logging.exception("Nieoczekiwany błąd E.W.A.")
            self._reply(500, {"error": "Błąd wewnętrzny E.W.A."})


if __name__ == "__main__":
    print(f"E.W.A. Brain — nasłuch na http://{HOST}:{PORT} (tylko USB)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
