#!/usr/bin/env python3
"""Jednorazowa konfiguracja istniejącego brokera i klucza HTTP na Pi 5."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Eva-AI" / "EWA_Brain"))
from modules.api_auth import read_key
from modules.smartroom import read_config, validate_config


def save_private(path, text):
    if path.is_symlink():
        raise ValueError(f"Plik nie może być dowiązaniem: {path}")
    previous = path.read_bytes() if path.exists() else None
    content = text.encode("utf-8")
    if previous == content:
        path.chmod(0o600)
        return
    if previous is not None:
        backups = Path.home() / "ewa-backups" / "mqtt"
        backups.mkdir(parents=True, exist_ok=True)
        fd, backup = tempfile.mkstemp(prefix=path.name + "-", dir=backups)
        with os.fdopen(fd, "wb") as output:
            output.write(previous)
        print(f"Kopia poprzedniej konfiguracji: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mqtt-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="adres TEGO SAMEGO brokera, z którym łączy się ESP32")
    parser.add_argument("--port", type=int, help="domyślnie 1883")
    parser.add_argument("--username", help="użytkownik MQTT; pusty tekst oznacza brak logowania")
    args = parser.parse_args(argv)
    folder = ROOT / "Eva-AI" / "EWA_Brain" / "config"
    old = read_config(folder / "mqtt.local.json") or {}
    host = args.host
    if host is None:
        default = old.get("host", "")
        host = input(f"Adres istniejącego brokera MQTT (jak w ESP32){' [' + default + ']' if default else ''}: ").strip() or default
    username = args.username
    if username is None:
        username = input("Użytkownik MQTT (Enter = bez logowania): ").strip()
    password = getpass.getpass("Hasło MQTT: ") if username else ""
    config = validate_config({"host": host, "port": args.port if args.port is not None else old.get("port", 1883),
                              "username": username, "password": password})
    key_path = folder / "api.key"
    key = read_key(key_path) or secrets.token_urlsafe(32)
    save_private(key_path, key + "\n")
    save_private(folder / "mqtt.local.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print("Zapisano lokalną konfigurację MQTT i klucz HTTP. Nie wysłano żadnej komendy do LED-ów.")
    print("Na Pi 5: sudo systemctl restart ewa-brain")
    print("Na Pi 3 skopiuj config/api.key do Eva-hand/api.key zgodnie ze START_ETAP7_MQTT.md.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, UnicodeError, EOFError) as exc:
        print(f"Błąd konfiguracji: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nPrzerwano konfigurację.")
        raise SystemExit(130)
