#!/usr/bin/env python3
"""Kopia konfiguracji i pamięci Pi5. Używaj wrapperów, które zatrzymują usługę."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_FILES = ("config/Modelfile", "config/brain_rules.txt", "memory/projects.json",
               "memory/projects.json.bak", "memory/conversations.db")
EXPORT = "metadata/ollama.modelfile.txt"
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def brain_dir(repo: Path) -> Path:
    brain = repo / "Eva-AI" / "EWA_Brain"
    if not (brain / "server.py").is_file():
        raise ValueError("Nie znaleziono katalogu EWA_Brain w repozytorium.")
    return brain


def safe_destination(brain: Path, relative: str) -> Path:
    path = brain / relative
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"Odmowa zapisu/odczytu dowiązania: {relative}")
    if not path.resolve().is_relative_to(brain.resolve()):
        raise ValueError("Ścieżka wychodzi poza katalog EWA_Brain.")
    return path


def backup(repo: Path, output_dir: Path, export_model: bool = True) -> Path:
    brain = brain_dir(repo)
    output_dir = output_dir.expanduser().resolve()
    if output_dir.is_relative_to(repo.resolve()):
        raise ValueError("Kopie zawierają prywatną pamięć. Wybierz katalog poza repo, np. ~/ewa-backups.")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {}
    with tempfile.TemporaryDirectory(prefix="ewa-state-") as temporary:
        for relative in STATE_FILES:
            source = safe_destination(brain, relative)
            if not source.exists():
                continue
            if not source.is_file() or source.stat().st_size > MAX_FILE:
                raise ValueError(f"Nieprawidłowy lub zbyt duży plik: {relative}")
            if relative.endswith(".db"):
                target = Path(temporary) / "snapshot.db"
                # API SQLite obejmuje zatwierdzone zmiany z WAL; wynik jest samodzielną bazą.
                with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
                    with closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise ValueError("Baza SQLite nie przeszła quick_check.")
                data = target.read_bytes()
            else:
                data = source.read_bytes()
            if len(data) > MAX_FILE:
                raise ValueError(f"Zbyt duży plik: {relative}")
            payload[relative] = data
    if export_model:
        try:
            result = subprocess.run(["ollama", "show", "ewa", "--modelfile"],
                                    capture_output=True, timeout=15, check=True)
            if len(result.stdout) <= MAX_FILE:
                payload[EXPORT] = result.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    if not any(name in payload for name in STATE_FILES):
        raise ValueError("Brak konfiguracji lub pamięci do skopiowania.")
    if sum(map(len, payload.values())) > MAX_TOTAL:
        raise ValueError("Kopia przekracza limit 128 MiB.")
    manifest = {"format": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
                "weights_included": False}
    payload["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = output_dir / f"ewa-state-{stamp}-{secrets.token_hex(3)}.tar.gz"
    try:
        with open(target, "xb") as output:
            os.chmod(target, 0o600)
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                for name, data in payload.items():
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), 0o600
                    archive.addfile(info, io.BytesIO(data))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def verified_payload(archive_path: Path) -> dict[str, bytes]:
    allowed = set(STATE_FILES) | {EXPORT, "manifest.json"}
    payload = {}
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for item in archive:
            if item.name not in allowed or not item.isfile() or item.name in payload:
                raise ValueError(f"Niedozwolony lub powtórzony wpis: {item.name}")
            total += item.size
            if not 0 <= item.size <= MAX_FILE or total > MAX_TOTAL:
                raise ValueError("Archiwum przekracza dozwolony rozmiar.")
            with archive.extractfile(item) as stream:
                payload[item.name] = stream.read(MAX_FILE + 1)
            if len(payload[item.name]) != item.size:
                raise ValueError("Niepełny plik w archiwum.")
    try:
        manifest = json.loads(payload.pop("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != 1:
            raise ValueError("Nieobsługiwany format kopii.")
        hashes = manifest["files"]
        if not isinstance(hashes, dict) or set(hashes) != set(payload):
            raise ValueError("Lista plików nie zgadza się z manifestem.")
        for name, data in payload.items():
            if hashlib.sha256(data).hexdigest() != hashes[name]:
                raise ValueError(f"Niezgodna suma kontrolna: {name}")
        if not any(name in payload for name in STATE_FILES):
            raise ValueError("Archiwum nie zawiera stanu E.W.A.")
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Niepoprawny manifest kopii.") from exc
    for name in ("memory/projects.json", "memory/projects.json.bak"):
        if name in payload:
            if not isinstance(json.loads(payload[name]), dict):
                raise ValueError(f"Niepoprawny JSON pamięci: {name}")
    if "memory/conversations.db" in payload:
        with tempfile.TemporaryDirectory(prefix="ewa-verify-") as temporary:
            database = Path(temporary) / "snapshot.db"
            database.write_bytes(payload["memory/conversations.db"])
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Niepoprawna baza SQLite w kopii.")
    return payload


def restore(repo: Path, archive_path: Path) -> list[str]:
    payload = verified_payload(archive_path)
    brain = brain_dir(repo)
    # Sprawdź wszystkie ścieżki przed pierwszą zmianą. Nigdy nie używaj extractall.
    paths = {name: safe_destination(brain, name) for name in STATE_FILES if name in payload}
    with tempfile.TemporaryDirectory(prefix=".ewa-restore-", dir=brain) as temporary:
        for index, (name, destination) in enumerate(paths.items()):
            staged = Path(temporary) / str(index)
            staged.write_bytes(payload[name])
            staged.chmod(0o600)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
        if "memory/conversations.db" in paths:
            for suffix in ("-wal", "-shm"):
                Path(str(paths["memory/conversations.db"]) + suffix).unlink(missing_ok=True)
    return list(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "verify", "restore"))
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "ewa-backups")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            print(backup(args.repo, args.output_dir))
        else:
            if args.archive is None:
                parser.error("Podaj ścieżkę archiwum.")
            if args.command == "verify":
                verified_payload(args.archive)
                print("Kopia: poprawne pliki, sumy kontrolne i format danych.")
            else:
                print("Przywrócono: " + ", ".join(restore(args.repo, args.archive)))
        return 0
    except (OSError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
        print(f"Błąd: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
