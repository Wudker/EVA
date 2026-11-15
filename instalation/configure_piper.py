#!/usr/bin/env python3
"""Włącz Piper w istniejącym audio.local.json; zachowaj pozostałe ustawienia."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Eva-hand"))
from speech_output import DEFAULT_PIPER_MODEL, DEFAULT_PIPER_PYTHON, SpeechError, piper_paths
from voice_client import parse_args


def configure(path, updates, backup_dir):
    previous = path.read_bytes() if path.exists() else None
    config = json.loads(previous.decode("utf-8")) if previous is not None else {}
    if not isinstance(config, dict):
        raise ValueError("Konfiguracja musi być obiektem JSON")
    config.update(updates)
    updated = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".audio-config-", suffix=".json", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(updated)
        parse_args(["--config", str(temp_path)])
        if previous == updated:
            return None, False
        backup = None
        if previous is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_fd, name = tempfile.mkstemp(prefix="audio-before-piper-", suffix=".json", dir=backup_dir)
            backup = Path(name)
            with os.fdopen(backup_fd, "wb") as output:
                output.write(previous)
        os.replace(temp_path, path)
        return backup, True
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("piper", "espeak"), default="piper")
    parser.add_argument("--piper-python", default=DEFAULT_PIPER_PYTHON)
    parser.add_argument("--piper-model", default=DEFAULT_PIPER_MODEL)
    parser.add_argument("--device", help="wejście ALSA; pominięte zachowuje obecną wartość")
    parser.add_argument("--output-device", help="wyjście ALSA; pominięte zachowuje obecną wartość")
    args = parser.parse_args()
    try:
        updates = {"tts_engine": args.engine, "speak": True, "stt_mode": "auto", "tts_fallback": True}
        if args.engine == "piper":
            python, model = piper_paths(args.piper_python, args.piper_model)
            updates.update(piper_python=str(python), piper_model=str(model))
        for key in ("device", "output_device"):
            if getattr(args, key) is not None:
                updates[key] = getattr(args, key)
        path = ROOT / "Eva-hand" / "audio.local.json"
        backup, changed = configure(path, updates, Path.home() / "ewa-backups" / "audio")
        if backup:
            print(f"Kopia poprzednich ustawień: {backup}")
        print(f"{'Zapisano' if changed else 'Już skonfigurowano'}: {path}")
        print(f"Silnik mowy: {args.engine}. Ustawienia miksera nie zostały zmienione.")
        return 0
    except (OSError, ValueError, SpeechError) as exc:
        print(f"Błąd konfiguracji: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
