import json
import tempfile
import unittest
from pathlib import Path

from modules.brain import Brain, _grounded_in_user
from modules.memory_manager import db_connect, get_pending


def answer(text="Odpowiadam.", kind="answer", action="none", pid="", val="", old="", alias="", interpretation=""):
    return {"type": kind, "text": text, "interpretation": interpretation,
            "clarification_project_id": pid if kind == "clarify" else "",
            "clarification_alias": alias, "memory_action": action,
            "memory_project_id": pid if kind != "clarify" else "",
            "memory_value": val, "memory_old_value": old}


class FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def __call__(self, history, text, context, note):
        self.inputs.append((list(history), text, context, note))
        if not self.outputs:
            raise AssertionError("Unexpected additional Ollama call")
        return self.outputs.pop(0)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.projects = base / "projects.json"
        self.db = base / "conversations.db"
        self.projects.write_text(json.dumps({"schema_version": 1, "projects": [
            {"id": "smartroom", "name": "SmartRoom", "aliases": [],
             "facts": ["SmartRoom wykorzystuje MQTT."]}]}, ensure_ascii=False), encoding="utf-8")

    def build(self, *messages):
        m = FakeModel(messages)
        return Brain(self.projects, self.db, m), m

    def test_auto_fact_explicit_and_persistent(self):
        brain, _ = self.build(answer("Rozumiem.", action="add_fact", pid="smartroom",
                                     val="W SmartRoom dodano czujnik temperatury."))
        r = brain.handle("Do projektu SmartRoom dodałem czujnik temperatury.")
        self.assertEqual(r["memory"], "saved")
        self.assertIn("czujnik temperatury", str(brain.store.project("smartroom")["facts"]))
        another = Brain(self.projects, self.db, FakeModel([]))
        self.assertEqual(len(another.store.project("smartroom")["facts"]), 2)
        with db_connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 1)

    def test_ungrounded_hallucination_is_ignored(self):
        brain, _ = self.build(answer(action="add_fact", pid="smartroom",
                                     val="W SmartRoom jest reaktor termojądrowy."))
        r = brain.handle("Do projektu SmartRoom dodałem czujnik temperatury")
        self.assertEqual(r["memory"], "none")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 1)

    def test_ambiguous_fact_asks_then_accepts(self):
        brain, mock = self.build(answer(action="add_fact", pid="smartroom",
                                        val="SmartRoom wykorzystuje czujnik ruchu."))
        r = brain.handle("Chyba SmartRoom wykorzystuje czujnik ruchu")
        self.assertEqual(r["type"], "clarify")
        self.assertEqual(r["memory"], "pending_confirmation")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 1)
        r = brain.handle("tak")
        self.assertEqual(r["memory"], "saved")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 2)
        self.assertEqual(len(mock.inputs), 1)

    def test_pending_no_and_new_topic(self):
        brain, mock = self.build(answer(action="add_fact", pid="smartroom",
                                        val="SmartRoom wykorzystuje czujnik ruchu."), answer())
        brain.handle("Chyba SmartRoom wykorzystuje czujnik ruchu")
        r = brain.handle("nie")
        self.assertEqual(r["memory"], "rejected")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 1)
        self.assertEqual(brain.handle("Dziękuję")["type"], "answer")
        self.assertEqual(len(mock.inputs), 2)

    def test_alias_after_clarification_confirmation(self):
        brain, mock = self.build(answer("Czy chodzi o SmartRoom?", kind="clarify", pid="smartroom",
                                        alias="small rum", interpretation="SmartRoom"),
                                 answer("Nie mam jeszcze odczytu z MQTT."))
        first = brain.handle("Podaj stan small rum")
        self.assertEqual(first["type"], "clarify")
        self.assertEqual(brain.store.project("smartroom")["aliases"], [])
        second = brain.handle("tak")
        self.assertEqual(second["type"], "answer")
        self.assertEqual(brain.store.project("smartroom")["aliases"], ["small rum"])
        self.assertIn("potwierdził", mock.inputs[1][3])
        self.assertIn("aliasy: small rum", brain.store.get_context("small rum", []))

    def test_no_alias_from_unconfirmed_or_fake_text(self):
        brain, mock = self.build(answer("SmartRoom?", kind="clarify", pid="smartroom",
                                        alias="false name", interpretation="SmartRoom"))
        brain.handle("Podaj stan small rum")
        # To potwierdzenie nie może dopisać aliasu niewystępującego w wypowiedzi.
        with db_connect(self.db) as db:
            self.assertIsNotNone(get_pending(db, "ewa-main"))
        brain.model_client = FakeModel([answer("Nie mam statusu MQTT.")])
        brain.handle("tak")
        self.assertEqual(brain.store.project("smartroom")["aliases"], [])

    def test_replacement_requires_approval_and_exact_old(self):
        brain, _ = self.build(answer(action="replace_fact", pid="smartroom",
                                     val="SmartRoom wykorzystuje HTTP.", old="SmartRoom wykorzystuje MQTT."))
        r = brain.handle("W SmartRoom zmieniłem komunikację z MQTT na HTTP")
        self.assertEqual(r["memory"], "pending_confirmation")
        self.assertEqual(brain.store.project("smartroom")["facts"], ["SmartRoom wykorzystuje MQTT."])
        brain.handle("tak")
        self.assertEqual(brain.store.project("smartroom")["facts"], ["SmartRoom wykorzystuje HTTP."])

    def test_secret_guard(self):
        brain, _ = self.build(answer(action="add_fact", pid="smartroom",
                                     val="SmartRoom password xyz123"))
        r = brain.handle("Do projektu SmartRoom dodałem password xyz123")
        self.assertNotEqual(r["memory"], "saved")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 1)

    def test_question_does_not_save(self):
        brain, _ = self.build(answer(action="add_fact", pid="smartroom",
                                     val="SmartRoom posiada czujnik ruchu."))
        r = brain.handle("Czy SmartRoom posiada czujnik ruchu?")
        self.assertEqual(r["memory"], "none")

    def test_no_duplicate_fact(self):
        brain, _ = self.build(answer(action="add_fact", pid="smartroom", val="SmartRoom wykorzystuje MQTT."))
        r = brain.handle("Zapamiętaj: SmartRoom wykorzystuje MQTT.")
        self.assertEqual(r["memory"], "skipped")
        self.assertEqual(len(brain.store.project("smartroom")["facts"]), 1)

    def test_new_project_requires_approval(self):
        brain, _ = self.build(answer(action="add_project", pid="robotdog", val="RobotDog"))
        r = brain.handle("Rozpoczynam nowy projekt RobotDog")
        self.assertEqual(r["memory"], "pending_confirmation")
        brain.handle("tak")
        self.assertIsNotNone(brain.store.project("robotdog"))

    def test_atomic_backup_and_audit(self):
        brain, _ = self.build(answer(action="add_fact", pid="smartroom",
                                     val="W SmartRoom dodano czujnik temperatury."))
        brain.handle("Do projektu SmartRoom dodałem czujnik temperatury")
        self.assertTrue(self.projects.with_suffix(".json.bak").exists())

    def test_session_isolation(self):
        brain, m = self.build(answer("Witaj."), answer("Witaj ponownie."))
        brain.handle("Cześć", session_id="one")
        brain.handle("Cześć", session_id="two")
        self.assertEqual(len(m.inputs[1][0]), 0)


if __name__ == "__main__":
    unittest.main()
