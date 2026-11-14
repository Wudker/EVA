import hashlib
import io
import json
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import state


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.brain = self.repo / "Eva-AI/EWA_Brain"
        (self.brain / "config").mkdir(parents=True)
        (self.brain / "memory").mkdir()
        (self.brain / "server.py").write_text("# test")
        (self.brain / "config/brain_rules.txt").write_text("Odpowiadaj po polsku.")
        (self.brain / "config/Modelfile").write_text("FROM qwen2.5:3b\n")
        (self.brain / "memory/projects.json").write_text(json.dumps({"projects": [{"name": "SmartRoom"}]}))
        self.dbpath = self.brain / "memory/conversations.db"
        with closing(sqlite3.connect(self.dbpath)) as db:
            db.execute("CREATE TABLE messages(text)")
            db.execute("INSERT INTO messages VALUES ('zapamiętano')")
            db.commit()

    def snapshot(self):
        return state.backup(self.repo, self.root / "backups", export_model=False)

    def test_roundtrip_preserves_state_and_does_not_touch_code(self):
        archive = self.snapshot()
        (self.brain / "memory/projects.json").write_text('{}')
        with closing(sqlite3.connect(self.dbpath)) as db:
            db.execute("DELETE FROM messages")
            db.commit()
        Path(str(self.dbpath) + "-wal").write_bytes(b"old wal")
        state.restore(self.repo, archive)
        self.assertEqual(json.loads((self.brain / "memory/projects.json").read_text())["projects"][0]["name"], "SmartRoom")
        with closing(sqlite3.connect(self.dbpath)) as db:
            self.assertEqual(db.execute("SELECT text FROM messages").fetchone()[0], "zapamiętano")
        self.assertFalse(Path(str(self.dbpath) + "-wal").exists())
        self.assertEqual((self.brain / "server.py").read_text(), "# test")
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

    def test_committed_wal_is_included_in_standalone_database(self):
        with closing(sqlite3.connect(self.dbpath)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO messages VALUES ('w WAL')")
            db.commit()
            archive = self.snapshot()
            payload = state.verified_payload(archive)
        restored = self.root / "standalone.db"
        restored.write_bytes(payload["memory/conversations.db"])
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_backup_inside_repo_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "poza repo"):
            state.backup(self.repo, self.repo / "backups", export_model=False)

    def malicious_archive(self, name, data=b"x", link=False):
        output = self.root / "bad.tar.gz"
        with tarfile.open(output, "w:gz") as archive:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if link:
                info.type, info.linkname, info.size = tarfile.SYMTYPE, "/tmp/outside", 0
            archive.addfile(info, None if link else io.BytesIO(data))
        return output

    def test_traversal_and_links_rejected_without_changes(self):
        for name, link in (("../outside", False), ("/tmp/outside", False), ("memory/projects.json", True)):
            with self.subTest(name=name, link=link):
                archive = self.malicious_archive(name, link=link)
                with self.assertRaises(ValueError):
                    state.restore(self.repo, archive)
                self.assertIn("SmartRoom", (self.brain / "memory/projects.json").read_text())

    def test_checksum_mismatch_rejected_before_writes(self):
        payload = state.verified_payload(self.snapshot())
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
        payload["config/brain_rules.txt"] = b"tampered"
        payload["manifest.json"] = json.dumps({"format": 1, "files": hashes}).encode()
        archive_path = self.root / "changed.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, data in payload.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, "suma kontrolna"):
            state.restore(self.repo, archive_path)
        self.assertEqual((self.brain / "config/brain_rules.txt").read_text(), "Odpowiadaj po polsku.")

    def test_destination_symlink_rejected_before_any_write(self):
        archive = self.snapshot()
        target = self.brain / "memory/projects.json"
        target.unlink()
        outside = self.root / "outside.json"
        outside.write_text('{"keep": true}')
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "dowiązania"):
            state.restore(self.repo, archive)
        self.assertEqual(outside.read_text(), '{"keep": true}')


if __name__ == "__main__":
    unittest.main()
