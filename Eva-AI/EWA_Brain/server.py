"""E.W.A. Brain API: USB, lokalny model i opcjonalne sterowanie LED przez MQTT."""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from modules.brain import Brain, OllamaError
from modules.api_auth import read_key
from modules.smartroom import read_config, load_lights

HOST = os.getenv("EVA_USB_IP", "10.77.77.1")
PORT = int(os.getenv("EVA_PORT", "8000"))
CONFIG = Path(__file__).resolve().parent / "config"
API_KEY = read_key(CONFIG / "api.key")
MQTT_CONFIG = read_config(CONFIG / "mqtt.local.json")
if MQTT_CONFIG is not None and API_KEY is None:
    raise RuntimeError("MQTT wymaga klucza HTTP. Uruchom python3 instalation/configure_mqtt.py")
brain = Brain(lights=load_lights(MQTT_CONFIG))
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
        info["auth_required"] = API_KEY is not None
        info["mqtt_enabled"] = brain.lights.enabled
        self._reply(200 if info["status"] == "ok" else 503, info)

    def do_POST(self):
        if self.path != "/chat":
            return self._reply(404, {"error": "Not found"})
        if API_KEY is not None and not hmac.compare_digest(
                self.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + API_KEY).encode("ascii")):
            return self._reply(401, {"error": "Brak poprawnego klucza API. Skopiuj config/api.key z Pi 5 do Eva-hand/api.key na Pi 3."})
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
    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        if os.getenv("EVA_WARMUP", "1") == "1":
            threading.Thread(target=brain.warmup, name="ewa-warmup", daemon=True).start()
        else:
            brain.warmup_state = "disabled"
        print(f"E.W.A. Brain — nasłuch na http://{HOST}:{PORT} (tylko USB)", flush=True)
        server.serve_forever()
