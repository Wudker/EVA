"""Bezpieczny podgląd historii i pamięci, kopie danych. Nie wymaga uruchamiania Qwena."""
import argparse
import json
from contextlib import closing
from modules.brain import DB_PATH, PROJECTS_PATH
from modules.memory_manager import MemoryStore, db_connect

p = argparse.ArgumentParser()
p.add_argument("action", choices=["projects", "history", "events", "pending"])
p.add_argument("--session", default="ewa-main")
p.add_argument("--limit", type=int, default=15)
args = p.parse_args()
s = MemoryStore(PROJECTS_PATH, DB_PATH)
if args.action == "projects":
    print(json.dumps(s.load(), ensure_ascii=False, indent=2))
else:
    with closing(db_connect(DB_PATH)) as db:
        if args.action == "history":
            rows = db.execute("SELECT created_at,role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                              (args.session, max(1, min(args.limit, 100)))).fetchall()
            for row in reversed(rows):
                print(f"[{row['created_at']}] {row['role']}: {row['content']}")
        elif args.action == "events":
            rows = db.execute("SELECT id,created_at,action,project_id,payload FROM memory_events ORDER BY id DESC LIMIT ?",
                              (max(1, min(args.limit, 100)),)).fetchall()
            for row in rows:
                print(f"#{row['id']} {row['created_at']}: {row['action']} {row['project_id']} {row['payload']}")
        else:
            rows = db.execute("SELECT session_id,kind,payload FROM pending ORDER BY created_at DESC LIMIT ?",
                              (max(1, min(args.limit, 100)),)).fetchall()
            for row in rows:
                print(row['session_id'], row['kind'], row['payload'])
