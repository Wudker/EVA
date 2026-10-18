"""Rozmowa tekstowa przez API E.W.A.; nie uruchamia drugiej instancji pamięci."""
import json
import os
import urllib.error
import urllib.request

URL = os.getenv("EVA_BRAIN_URL", "http://10.77.77.1:8000/chat")
SID = os.getenv("EVA_SESSION_ID", "ewa-main")

print("E.W.A. — rozmowa tekstowa. /exit kończy program.")
while True:
    try:
        text = input("Ty> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if text in {"/exit", "/bye", "/quit"}:
        break
    if not text:
        continue
    request = urllib.request.Request(
        URL, data=json.dumps({"session_id": SID, "text": text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=390) as response:
            result = json.load(response)
        print("E.W.A.>", result["text"])
        if result.get("memory") in {"saved", "pending_confirmation", "rejected"}:
            print("        [pamięć:", result["memory"] + "]")
    except urllib.error.HTTPError as exc:
        print("Błąd HTTP:", exc.code, exc.read().decode("utf-8", errors="replace")[:400])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print("Brak połączenia z E.W.A.:", exc)
