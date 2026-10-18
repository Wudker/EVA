"""Pamięć E.W.A.: projekty JSON + historia SQLite; zapis atomowy i audyt."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
from pathlib import Path

_lock = threading.RLock()
PROJECT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SENSITIVE = re.compile(r"\b(?:hasl[oa]|password|passwd|secret|sekret|token|api[\s_-]*key)\b|sk-[A-Za-z0-9_-]{12,}", re.I)


def normal(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                            if not unicodedata.combining(c)).split())


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=20) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id,id);
        CREATE TABLE IF NOT EXISTS pending (
            session_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('memory','clarification')),
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS memory_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            snapshot_before TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)


def db_connect(db_path: Path):
    db = sqlite3.connect(db_path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    return db


def save_turn(db, session_id: str, user: str, answer: str) -> None:
    db.executemany("INSERT INTO messages(session_id,role,content) VALUES(?,?,?)", [
        (session_id, "user", user), (session_id, "assistant", answer)])
    db.commit()


def recent(db, session_id: str, limit: int = 8) -> list[dict]:
    rows = db.execute("SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                      (session_id, limit)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def get_pending(db, session_id: str):
    row = db.execute("SELECT kind,payload FROM pending WHERE session_id=?", (session_id,)).fetchone()
    return (row["kind"], json.loads(row["payload"])) if row else None


def set_pending(db, session_id: str, kind: str, payload: dict):
    db.execute("INSERT OR REPLACE INTO pending(session_id,kind,payload) VALUES(?,?,?)",
               (session_id, kind, json.dumps(payload, ensure_ascii=False)))
    db.commit()


def clear_pending(db, session_id: str):
    db.execute("DELETE FROM pending WHERE session_id=?", (session_id,))
    db.commit()


class MemoryStore:
    def __init__(self, projects_path: Path, db_path: Path):
        self.projects_path = Path(projects_path)
        self.db_path = Path(db_path)
        init_db(self.db_path)
        if not self.projects_path.exists():
            template = self.projects_path.with_name("projects.example.json")
            if not template.exists():
                raise FileNotFoundError(f"Brak szablonu projektów: {template}")
            shutil.copy2(template, self.projects_path)
        self.load()  # walidacja na starcie

    def load(self) -> dict:
        if self.projects_path.stat().st_size > 512_000:
            raise ValueError("projects.json przekracza limit 500 KB")
        data = json.loads(self.projects_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("projects"), list):
            raise ValueError("Niepoprawna struktura projects.json")
        seen = set()
        for project in data["projects"]:
            if not isinstance(project, dict) or not PROJECT_ID.fullmatch(str(project.get("id", ""))):
                raise ValueError("Niepoprawne id projektu")
            if project["id"] in seen:
                raise ValueError("Powtórzone id projektu")
            seen.add(project["id"])
            if not isinstance(project.get("name"), str) or not isinstance(project.get("aliases"), list) or not isinstance(project.get("facts"), list):
                raise ValueError("Niepoprawne pole projektu")
        return data

    def project(self, project_id: str):
        return next((p for p in self.load()["projects"] if p["id"] == project_id), None)

    def get_context(self, question: str, history: list[dict], max_projects: int = 2) -> str:
        """Krótki indeks + fakty tylko z pasujących projektów."""
        data = self.load()
        names = [f"{p['name']} (id={p['id']})" +
                 (f", aliasy: {', '.join(p['aliases'][:12])}" if p['aliases'] else "")
                 for p in data["projects"]]
        text = normal(question)
        selected = [p for p in data["projects"] if any(
            normal(x) in text for x in [p["name"], *p["aliases"]] if len(normal(x)) > 2)]
        if not selected:
            history_text = normal(" ".join(msg["content"] for msg in history[-4:]))
            selected = [p for p in data["projects"] if any(
                normal(x) in history_text for x in [p["name"], *p["aliases"]] if len(normal(x)) > 2)]
        output = ["INDEKS ZNANYCH PROJEKTÓW: " + "; ".join(names)[:1600]]
        for p in selected[:max_projects]:
            facts = "; ".join(str(v) for v in p["facts"][:18])[:1800]
            output.append(f"PROJEKT {p['name']} ({p['id']}): {facts}")
        return "\n".join(output)

    @staticmethod
    def _valid_text(value: str, maximum: int = 240) -> str:
        if not isinstance(value, str) or not 3 <= len(value.strip()) <= maximum:
            raise ValueError("Brak wartości lub przekroczony limit znaków")
        value = value.strip()
        if "\n" in value or "\r" in value or SENSITIVE.search(normal(value)):
            raise ValueError("Nie zapisuję wielowierszowych wpisów ani informacji mogących być sekretem")
        return value

    @staticmethod
    def _find_project(data: dict, project_id: str) -> dict:
        return next((p for p in data["projects"] if p["id"] == project_id), None)

    def apply(self, action: dict, db, session_id: str) -> tuple[bool, str]:
        """Zapis tylko w ramach jawnej listy akcji. Snapshot przed każdą zmianą w SQLite."""
        with _lock:
            data = self.load()
            before = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            kind = action.get("action")
            project_id = action.get("project_id", "")
            if kind not in {"add_fact", "replace_fact", "add_alias", "add_project"}:
                raise ValueError("Nieznana akcja pamięci")
            if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
                raise ValueError("Niepoprawne id projektu")
            p = self._find_project(data, project_id)
            if kind == "add_project":
                if p:
                    return False, "Taki projekt już istnieje."
                name = self._valid_text(action.get("value", ""), 80)
                data["projects"].append({"id": project_id, "name": name, "aliases": [], "facts": []})
                description = f"Dodano projekt {name}."
            else:
                if p is None:
                    raise ValueError("Nieznany projekt")
                value = self._valid_text(action.get("value", ""))
                if kind == "add_fact":
                    if any(normal(f) == normal(value) for f in p["facts"]):
                        return False, "Ta informacja jest już zapisana."
                    if len(p["facts"]) >= 300:
                        raise ValueError("Osiągnięto limit faktów projektu")
                    p["facts"].append(value)
                    description = f"Dodano informację do {p['name']}."
                elif kind == "replace_fact":
                    old = self._valid_text(action.get("old_value", ""))
                    index = next((i for i, f in enumerate(p["facts"]) if normal(f) == normal(old)), None)
                    if index is None:
                        raise ValueError("Nie znaleziono dokładnie wskazanego starego wpisu")
                    p["facts"][index] = value
                    description = f"Zaktualizowano informację w {p['name']}."
                else:
                    # Nie dopisuj aliasu, który oznacza inny projekt.
                    value = self._valid_text(value, 64)
                    if len(value.split()) > 4 or any(w in normal(value).split() for w in
                            {"podaj", "stan", "wlacz", "wylacz", "pokaz", "status", "ledy"}):
                        raise ValueError("Alias wygląda jak całe polecenie, nie nazwa")
                    if normal(value) in {normal(p["name"]), *(normal(a) for a in p["aliases"])}:
                        return False, "Alias jest już znany."
                    for other in data["projects"]:
                        if other["id"] != project_id and normal(value) in {
                                normal(other["name"]), *(normal(a) for a in other["aliases"])}:
                            raise ValueError("Alias należy już do innego projektu")
                    if len(p["aliases"]) >= 100:
                        raise ValueError("Osiągnięto limit aliasów")
                    p["aliases"].append(value)
                    description = f"Dodano alias '{value}' do {p['name']}."
            new_content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            if len(new_content.encode("utf-8")) > 512_000:
                raise ValueError("Przekroczono limit pliku projects.json")
            self._atomic_save(new_content)
            db.execute("INSERT INTO memory_events(session_id,action,project_id,payload,snapshot_before) VALUES(?,?,?,?,?)",
                       (session_id, kind, project_id, json.dumps(action, ensure_ascii=False), before))
            db.commit()
            return True, description

    def _atomic_save(self, content: str) -> None:
        self.projects_path.parent.mkdir(parents=True, exist_ok=True)
        if self.projects_path.exists():
            shutil.copy2(self.projects_path, self.projects_path.with_suffix(".json.bak"))
        fd, tmp = tempfile.mkstemp(dir=str(self.projects_path.parent), prefix=".projects-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.projects_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
