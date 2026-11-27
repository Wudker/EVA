"""Testy kontekstu i routingu AI, bez Ollamy, mikrofonu i połączenia z MQTT."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.brain import Brain
from modules import brain as b
from modules.light_commands import command_from_ai, parse_command
from modules.memory_manager import db_connect, set_pending


class Lights:
    enabled = True

    def __init__(self):
        self.commands = []

    def execute(self, command):
        self.commands.append(dict(command))
        return {"type": "answer", "text": "Stan pochodzi ze sterownika.", "source": "mqtt", "device_status": "confirmed"}


class CommandTests(unittest.TestCase):
    def test_polish_names_groups_and_brightness(self):
        cases = [
            ("Zapał ledy na szafce", "power", "shelf", 1),
            ("Czy możesz zapalić LED-y na szafce?", "power", "shelf", 1),
            ("Zapal wszystkie ledy", "power", "all", 1),
            ("Zgaś światło na półce i przy łóżku", "power", "all", 0),
            ("Ustaw intensywność oświetlenia na szafce na pięćdziesiąt procent", "brightness", "shelf", 128),
            ("Rozjaśnij na szafce o 20 procent", "adjust_brightness", "shelf", 20),
            ("Trochę ciemniej", "adjust_brightness", None, -10),
            ("Ustaw jasność na półce", "brightness", "shelf", None),
            ("Czy światło na półce jest włączone?", "status", "shelf", None),
        ]
        for text, action, target, value in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), {"action": action, "target": target, "value": value})

    def test_negations_descriptions_future_and_other_modules_are_not_commands(self):
        for text in ("Nie włącz światła na półce", "Jak włączyć ledy na szafce?",
                     "Gdybyś włączyła ledy na półce", "Włączyłem ledy na półce",
                     "Włącz światło na półce jutro", "Włącz ledy na półce o 17",
                     "Włącz muzykę", "Podaj stan projektu SmartRoom",
                     "Zapal półkę i zgaś łóżko"):
            with self.subTest(text=text):
                self.assertIsNone(parse_command(text))

    def test_ai_values_are_checked_without_clamping_or_mqtt_topics(self):
        for fields in ({"light_action": "shell"},
                       {"light_action": "power", "light_target": "smartroom/other/set", "light_value": "1"},
                       {"light_action": "brightness", "light_target": "bed", "light_value": "101"},
                       {"light_action": "power", "light_target": "bed", "light_value": "true"},
                       {"light_action": "status", "light_target": "bed", "light_value": "1"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                command_from_ai(fields)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        projects = root / "projects.json"
        projects.write_text(json.dumps({"schema_version": 1, "projects": [
            {"id": "smartroom", "name": "SmartRoom", "aliases": [], "facts": []}]}), encoding="utf-8")
        self.model = Mock(return_value={"type": "answer", "text": "Odpowiedź modelu."})
        self.lights = Lights()
        self.brain = Brain(projects, root / "history.db", self.model, self.lights)

    def ai(self, action="power", target="shelf", value="1", kind="answer"):
        self.model.return_value = {"type": kind, "text": "Tekst modelu nie potwierdza sprzętu.",
                                   "light_action": action, "light_target": target, "light_value": value}

    def test_simple_command_works_without_ai(self):
        result = self.brain.handle("Zapal ledy na szafce")
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")
        self.assertEqual(result["source"], "mqtt")
        self.model.assert_not_called()

    def test_ai_interprets_new_wording_and_server_supplies_final_answer(self):
        self.ai()
        result = self.brain.handle("Podświetl miejsce na szafce")
        self.assertEqual(result["interpretation_source"], "ai")
        self.assertEqual(result["text"], "Stan pochodzi ze sterownika.")
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")
        self.assertIn("NARZĘDZIE LED", self.model.call_args.args[3])

    def test_ollama_json_contract_routes_lights_in_both_formats(self):
        self.brain.model_client = b.call_ollama
        self.brain.light_client = b.call_ollama
        for mode in ("compact", "legacy"):
            answer = {"type": "answer", "text": "Interpretacja polecenia.",
                      "light_action": "brightness", "light_target": "bed", "light_value": "20"}
            if mode == "legacy":
                answer.update({key: "none" if key == "memory_action" else ""
                               for key in b.LEGACY_SCHEMA["required"] if key not in answer})
            raw = {"message": {"content": json.dumps(answer)}, "done": True}
            with self.subTest(mode=mode), patch.dict(os.environ, {"EVA_JSON_FORMAT": mode}), \
                    patch.object(b, "make_request_json", return_value=raw) as request:
                result = self.brain.handle("Ustaw światło przy łóżku na dwadzieścia procent", mode)
                self.assertEqual(result["source"], "mqtt")
                self.assertEqual(self.lights.commands[-1]["value"], 51)
                self.assertIn("light_action", request.call_args.args[1]["format"]["properties"])

    def test_followup_keeps_target_and_group(self):
        self.brain.handle("Zapal wszystkie ledy", "one")
        self.brain.handle("Trochę ciemniej", "one")
        self.assertEqual(self.lights.commands[-1]["target"], "all")
        self.assertEqual(self.lights.commands[-1]["value"], -10)
        self.model.assert_not_called()

    def test_context_is_per_session_and_expires(self):
        self.brain.handle("Zapal ledy na szafce", "one")
        result = self.brain.handle("Trochę ciemniej", "two")
        self.assertEqual(result["type"], "clarify")
        self.brain.light_context["one"]["expires_at"] = time.time() - 1
        self.assertEqual(self.brain.handle("Trochę ciemniej", "one")["type"], "clarify")
        self.assertEqual(len(self.lights.commands), 1)

    def test_new_topic_clears_context(self):
        self.brain.handle("Zapal ledy na szafce")
        self.brain.handle("Ile to dwa plus dwa?")
        self.assertEqual(self.brain.handle("Trochę ciemniej")["type"], "clarify")
        self.assertEqual(len(self.lights.commands), 1)

    def test_missing_device_and_value_can_be_supplied_in_next_turn(self):
        self.assertEqual(self.brain.handle("Włącz światło")["type"], "clarify")
        self.brain.handle("wszystkie")
        self.assertEqual(self.lights.commands[-1]["target"], "all")
        self.assertEqual(self.brain.handle("Ustaw jasność na szafce")["type"], "clarify")
        self.brain.handle("pięćdziesiąt procent")
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")
        self.assertEqual(self.lights.commands[-1]["value"], 128)

    def test_uncertain_ai_mapping_requires_confirmation(self):
        self.ai()
        result = self.brain.handle("Podświetl komodę")
        self.assertEqual(result["type"], "clarify")
        self.assertFalse(self.lights.commands)
        self.brain.handle("tak")
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")

    def test_model_clarification_cannot_execute_before_yes(self):
        self.ai(kind="clarify")
        self.assertEqual(self.brain.handle("Podświetl szafkę")["type"], "clarify")
        self.assertFalse(self.lights.commands)
        self.brain.handle("nie")
        self.assertFalse(self.lights.commands)

    def test_expired_clarification_does_not_execute(self):
        with db_connect(self.brain.db_path) as db:
            set_pending(db, "ewa-main", "clarification", {"tool": "led", "action": "power",
                        "target": "shelf", "value": 1, "needs_confirmation": True, "expires_at": time.time() - 1})
        self.assertEqual(self.brain.handle("tak")["type"], "clarify")
        self.assertFalse(self.lights.commands)

    def test_even_wrong_ai_output_cannot_execute_negation_or_hypothesis(self):
        self.ai()
        for text in ("Nie włącz światła na półce", "Jak włączyć ledy na szafce?",
                     "Włącz ledy na półce jutro", "Zapal półkę i zgaś łóżko"):
            with self.subTest(text=text):
                self.brain.handle(text)
        self.assertFalse(self.lights.commands)

    def test_model_cannot_expand_single_device_to_all_or_save_a_fact(self):
        self.ai(target="all")
        self.model.return_value.update(memory_action="add_fact", memory_project_id="smartroom", memory_value="LED-y są włączone")
        self.brain.handle("Podświetl miejsce na szafce")
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")
        self.assertEqual(self.brain.store.project("smartroom")["facts"], [])


if __name__ == "__main__":
    unittest.main()
