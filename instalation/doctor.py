#!/usr/bin/env python3
"""Diagnostyka bez zmiany konfiguracji. --chat opcjonalnie wysyła jedno pytanie testowe."""
import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def command(label, args):
    print(f"\n[{label}]", flush=True)
    if not shutil.which(str(args[0])):
        print("Brak:", args[0])
        return False
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        print((result.stdout + result.stderr).strip()[-5000:])
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        print(exc)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("pi5", "pi3"), required=True)
    parser.add_argument("--chat", action="store_true")
    args = parser.parse_args()
    ok = True
    command("System", ["uname", "-m"])
    command("Adresy", ["ip", "-br", "addr"])
    if args.role == "pi5":
        ok &= command("Usługi", ["systemctl", "is-active", "ollama", "ewa-brain"])
        ok &= command("Model ewa", ["ollama", "show", "ewa"])
        command("RAM", ["free", "-h"])
    else:
        ok &= command("Vosk", [str(REPO / "Eva-hand/.venv/bin/python"), "-c", "import vosk; print('Vosk: OK')"])
        ok &= command("Mikrofony", ["arecord", "-l"])
        ok &= command("Wyjścia audio", ["aplay", "-l"])
        ok &= command("Polski głos", ["espeak-ng", "--voices=pl"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://10.77.77.1:8000/health", timeout=8) as response:
            health = json.load(response)
        print("\n[/health]", health)
        ok &= health.get("status") == "ok"
        if args.chat:
            request = urllib.request.Request("http://10.77.77.1:8000/chat",
                data=json.dumps({"session_id": "ewa-diagnostic", "text": "Ile to dwa plus dwa?"}).encode(),
                headers={"Content-Type": "application/json"})
            started = time.monotonic()
            with opener.open(request, timeout=390) as response:
                answer = json.load(response)
            print(f"[/chat: {time.monotonic() - started:.1f} s]", answer)
            ok &= isinstance(answer.get("text"), str) and bool(answer["text"].strip())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print("\nBłąd API:", exc)
        ok = False
    print("\nWynik:", "OK" if ok else "część kontroli nie powiodła się")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
