"""Regresje na podstawie wypowiedzi z rzeczywistego logu E.W.A."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import brain as b
from modules.light_commands import parse_command, percent_in_utterance
import test_light_context as fixtures


class WordingTests(unittest.TestCase):
    def test_brightness_variants_from_log(self):
        for text in ("ustaw jasność wszystkich obwodów do poziomu pięćdziesiąt proc",
                     "ustaw jasność wszystkich obwodów na pięćdziesiąt proc.",
                     "ustaw poziom oświetlenia wszystkich obwodów na pięćdziesiąt procent",
                     "ustaw natężenie światła wszystkich LED-ów do 50%",
                     "ustaw jasność wszystkich obwodów pięćdziesiąt procent"):
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), {"action": "brightness", "target": "all", "value": 128})

    def test_number_is_not_a_suffix_of_invalid_number(self):
        for text in ("ustaw na sto pięćdziesiąt procent", "ustaw na minus pięć procent",
                     "ustaw na 150 procent", "ustaw na dziesięć pięć procent"):
            with self.subTest(text=text):
                self.assertIsNone(percent_in_utterance(text))
        self.assertEqual(percent_in_utterance("ustaw na osiem procent"), 8)


class ReplayTests(unittest.TestCase):
    setUp = fixtures.ContextTests.setUp
    ai = fixtures.ContextTests.ai

    def test_noisy_start_requires_one_confirmation(self):
        self.ai(action="brightness", target="all", value="80")
        self.assertEqual(self.brain.handle("usne ustaw jasność wszystkich obwodów osiemdziesiąt procent")["type"], "clarify")
        self.assertFalse(self.lights.commands)
        self.brain.handle("tak")
        self.assertEqual(len(self.lights.commands), 1)
        self.assertEqual(self.model.call_count, 1)

    def test_complete_proc_command_does_not_call_model(self):
        self.brain.handle("ustaw jasność wszystkich obwodów do poziomu pięćdziesiąt proc")
        self.assertEqual(self.lights.commands[-1]["value"], 128)
        self.assertEqual(self.lights.commands[-1]["target"], "all")
        self.model.assert_not_called()

    def test_level_uses_recent_all_target(self):
        self.brain.handle("zaświeć wszystkim światła")
        self.brain.handle("ustaw poziom oświetlenia na osiemdziesiąt procent")
        self.assertEqual(self.lights.commands[-1]["value"], 204)
        self.assertEqual(self.lights.commands[-1]["target"], "all")
        self.model.assert_not_called()

    def test_plain_text_question_is_saved_and_yes_executes_once(self):
        self.model.return_value = {"type": "clarify", "text":
            "Czy chodzi Ci o ustawienie jasności wszystkich obwodów do poziomu 80 procent?"}
        first = self.brain.handle("usne ustaw jasność wszystkich obwodów osiemdziesiąt proc")
        self.assertEqual(first["type"], "clarify")
        self.assertFalse(self.lights.commands)
        self.brain.handle("tak")
        self.assertEqual(len(self.lights.commands), 1)
        self.assertEqual(self.lights.commands[0]["value"], 204)
        self.assertEqual(self.model.call_count, 1)

    def test_garbled_number_is_requested_and_can_be_supplied(self):
        first = self.brain.handle("ustaw jasność wszystkich uwag lub na osi poza")
        self.assertEqual(first["type"], "clarify")
        self.brain.handle("pięćdziesiąt proc")
        self.assertEqual(self.lights.commands[-1]["value"], 128)
        self.assertEqual(self.lights.commands[-1]["target"], "all")
        self.model.assert_not_called()

    def test_ai_does_not_invent_a_number_from_old_turns(self):
        self.ai(action="brightness", target="all", value="80", kind="clarify")
        result = self.brain.handle("usne ustaw jasność wszystkich obwodów przodu")
        self.assertIn("Jaką jasność", result["text"])
        self.assertNotIn("80", result["text"])
        self.assertFalse(self.lights.commands)

    def test_unknown_circuit_b_does_not_become_all(self):
        self.brain.handle("zapal wszystkie ledy")
        self.ai(action="brightness", target="all", value="50")
        first = self.brain.handle("ustaw jasność obwodu b pięćdziesiąt procent")
        self.assertEqual(first["type"], "clarify")
        self.assertEqual(len(self.lights.commands), 1)
        self.brain.handle("przy łóżku")
        self.assertEqual(self.lights.commands[-1]["target"], "bed")
        self.assertEqual(self.lights.commands[-1]["value"], 128)

    def test_production_uses_short_required_intent_schema(self):
        general = Mock(side_effect=AssertionError("Zwykła rozmowa nie powinna obsługiwać tego polecenia"))
        self.brain.model_client = general
        self.brain.light_client = b.call_light_ollama
        raw = {"message": {"content": json.dumps({"action": "power", "target": "shelf", "value": "1", "confirm": False})}}
        with patch.object(b, "make_request_json", return_value=raw) as request:
            self.brain.handle("podświetl miejsce na szafce")
        payload = request.call_args.args[1]
        self.assertEqual(set(payload["format"]["required"]), {"action", "target", "value", "confirm"})
        self.assertEqual(len(payload["messages"]), 2)
        self.assertNotIn("PAMIĘĆ PROJEKTÓW", payload["messages"][0]["content"])
        self.assertEqual(self.lights.commands[-1]["target"], "shelf")

    def test_intent_json_cannot_silently_omit_action(self):
        raw = {"message": {"content": json.dumps({"target": "all", "value": "50", "confirm": False})}}
        with patch.object(b, "make_request_json", return_value=raw), self.assertRaises(b.OllamaError):
            b.call_light_ollama([], "polecenie", "")


if __name__ == "__main__":
    unittest.main()
