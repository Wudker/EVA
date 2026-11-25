"""Wspólny klucz HTTP dla prywatnego połączenia Pi 3 -> Pi 5."""
import re


def read_key(path):
    if not path.exists():
        return None
    key = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
        raise ValueError("Niepoprawny plik klucza API; uruchom configure_mqtt.py")
    return key
