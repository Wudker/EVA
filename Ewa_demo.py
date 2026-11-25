#!/usr/bin/env python3
"""Uruchom na Pi 3: python3 Ewa_demo.py. Enter nagrywa 8 s, q kończy rozmowę."""
import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    client = root / "Eva-hand" / "voice_client.py"
    # Nie używamy resolve() na interpreterze: musi zachować środowisko .venv.
    python = root / "Eva-hand" / ".venv" / "bin" / "python"
    if not python.is_file() or not client.is_file():
        print("Uruchom ten plik na Pi 3, w repozytorium zawierającym Eva-hand/.venv.", file=sys.stderr)
        return 1
    args = [str(python), str(client), "--speak", "--tts-engine", "piper-remote",
            "--stt-mode", "persistent", "--seconds", "8", "--no-tts-fallback", *sys.argv[1:]]
    try:
        os.execv(str(python), args)
    except OSError as exc:
        print(f"Nie można uruchomić klienta Ewy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
