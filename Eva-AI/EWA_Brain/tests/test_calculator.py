import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import Mock, patch

from modules import brain as b
from modules.calculator import calculate, evaluate, expression_from


class CalculatorTests(unittest.TestCase):
    def test_precedence_signed_values_and_spoken_polish(self):
        cases = [
            ("ile tu trzy razy trzy plus trzy", 12),
            ("Ile to trzy plus pięć?", 8), ("Ile to siedem razy osiem?", 56),
            ("Ile wynosi 20 - 8 / 2?", 16), ("8 / (2 + 2)", 2),
            ("20 / 5 * 2", 8), ("10 - 3 - 2", 5),
            ("minus trzy razy minus dwa", 6), ("3 * (4 + 5)", 27),
            ("trzy pomnożone przez siedem", 21), ("osiem podzielone przez dwa", 4),
            ("Oblicz: sto dwadzieścia trzy plus siedem", 130),
            ("dziewięćset dziewięćdziesiąt dziewięć minus dziewięćdziesiąt osiem", 901),
            ("Oblicz 007 + 01", 8), ("3 × 4 − 2", 10), ("8 ÷ 2", 4),
        ]
        for question, expected in cases:
            with self.subTest(question=question):
                result = calculate(question)
                self.assertEqual(result["type"], "answer")
                self.assertEqual(result["text"], f"Wynik to {expected}.")
                self.assertEqual(evaluate(result["expression"]), expected)

    def test_decimals_are_exact_before_explicit_output_rounding(self):
        cases = [("0,1 plus 0,2", "0,3", Fraction(3, 10)),
                 ("dwa przecinek zero pięć razy trzy", "6,15", Fraction(615, 100)),
                 ("dwa przecinek dwadzieścia pięć plus 0.05", "2,3", Fraction(23, 10)),
                 ("zero przecinek zero zero pięć razy dwa", "0,01", Fraction(1, 100))]
        for question, printed, exact in cases:
            with self.subTest(question=question):
                self.assertEqual(calculate(question)["text"], f"Wynik to {printed}.")
                self.assertEqual(evaluate(expression_from(question)), exact)
        self.assertEqual(calculate("1/3")["text"], "Wynik to około 0,333333.")
        self.assertEqual(calculate("1/1000000000000")["text"],
                         "Dokładny wynik to 1 podzielone przez 1000000000000.")

    def test_zero_division_and_bad_syntax_are_not_guessed(self):
        self.assertEqual(calculate("trzy dzielone przez zero")["text"], "Nie można dzielić przez zero.")
        for question in ("trzy plus", "trzy trzy plus dwa", "sto zero plus dwa", "3 + )2(",
                         "3/0 +", "2(3+4)", "2**3", "dwa przecinek plus trzy"):
            with self.subTest(question=question):
                self.assertEqual(calculate(question)["type"], "clarify")

    def test_only_whole_supported_math_questions_are_intercepted(self):
        for question in ("Ile jest w Polsce województw?", "Włącz światło w pokoju.",
                         "Do projektu SmartRoom dodałem 3 + 5 czujników.",
                         "3 + 5 i zapamiętaj wynik", "Czy trzy plus pięć to osiem?", "tak", "nie", "3",
                         "Oblicz trzy plus, nie, chyba razy...", "1e3 + 2", "__import__('os').system('id') + 1"):
            with self.subTest(question=question):
                self.assertIsNone(calculate(question))

    def test_bounded_resources_and_magnitude(self):
        for question in ("1000000000001+1", "1000000000000*2", "(" * 13 + "1+2" + ")" * 13,
                         "+".join(["1"] * 35), "0.1234567890123+1"):
            with self.subTest(question=question):
                result = calculate(question)
                self.assertEqual(result["type"], "clarify")
                self.assertIn("limit", result["text"])


class BrainCalculatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / "projects.json"
        self.projects.write_text(json.dumps({"schema_version": 1, "projects": [
            {"id": "smartroom", "name": "SmartRoom", "aliases": [], "facts": []}]}))
        self.model = Mock(side_effect=AssertionError("Calculator must not call AI"))
        self.brain = b.Brain(self.projects, self.root / "history.db", self.model)

    def test_corrects_previous_bad_model_history_without_touching_project_facts(self):
        before = self.projects.read_bytes()
        with closing(b.db_connect(self.brain.db_path)) as db:
            b.save_turn(db, "test", "Ile to trzy plus pięć?", "Siedem.")
        result = self.brain.handle("Ile to trzy plus pięć?", "test")
        self.assertEqual(result["text"], "Wynik to 8.")
        self.assertEqual(result["source"], "calculator")
        self.assertEqual(result["memory"], "none")
        self.assertNotIn("timings", result)
        self.model.assert_not_called()
        self.assertEqual(self.projects.read_bytes(), before)
        with closing(b.db_connect(self.brain.db_path)) as db:
            self.assertEqual(b.recent(db, "test")[-1]["content"], "Wynik to 8.")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 0)
            self.assertEqual(b.recent(db, "other"), [])

    def test_new_math_topic_cancels_pending_memory_without_accepting_it(self):
        with closing(b.db_connect(self.brain.db_path)) as db:
            b.set_pending(db, "test", "memory", {"action": "add_fact", "project_id": "smartroom",
                                                  "value": "Niepotwierdzony fakt"})
        self.brain.handle("7 razy 8", "test")
        self.assertEqual(self.brain.store.project("smartroom")["facts"], [])
        with closing(b.db_connect(self.brain.db_path)) as db:
            self.assertIsNone(b.get_pending(db, "test"))
        self.model.assert_not_called()

    def test_ordinary_question_still_reaches_ai_with_math_history(self):
        self.brain.handle("3*3+3", "test")
        self.model.side_effect = None
        self.model.return_value = {"type": "answer", "text": "Odpowiedź modelu"}
        self.assertEqual(self.brain.handle("Jak działa tranzystor?", "test")["text"], "Odpowiedź modelu")
        self.model.assert_called_once()
        self.assertEqual(self.model.call_args.args[0][-1]["content"], "Wynik to 12.")

    def test_real_chat_http_computes_when_ollama_requests_fail(self):
        spec = importlib.util.spec_from_file_location("calculator_test_server", b.ROOT / "server.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(b, "Brain", return_value=self.brain):
            spec.loader.exec_module(module)
        server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/chat",
                data=json.dumps({"text": "ile tu trzy razy trzy plus trzy", "session_id": "test"}).encode(),
                headers={"Content-Type": "application/json"})
            with patch.object(b, "make_request_json", side_effect=b.OllamaError("offline")) as ollama, \
                 patch.object(module.Handler, "log_message"), urllib.request.urlopen(request, timeout=2) as response:
                result = json.load(response)
            self.assertEqual(result["text"], "Wynik to 12.")
            self.assertEqual(result["expression"], "3 * 3 + 3")
            ollama.assert_not_called()
            self.model.assert_not_called()
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
