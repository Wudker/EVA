"""Kontrakt JSON, telemetria i pamięć. Nie oceniają jakości prawdziwego LLM."""
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from modules import brain as b


def raw(answer, **updates):
    result = {"message": {"content": json.dumps(answer, ensure_ascii=False)}, "done_reason": "stop",
              "total_duration": 2_000_000_000, "load_duration": 0,
              "prompt_eval_duration": 500_000_000, "eval_duration": 1_500_000_000,
              "prompt_eval_count": 10, "eval_count": 9}
    result.update(updates)
    return result


class FormatTests(unittest.TestCase):
    def test_minimal_answer_gets_safe_defaults_and_timings(self):
        with patch.object(b, "make_request_json", return_value=raw({"type": "answer", "text": "Osiem."})) as request:
            answer = b.call_ollama([], "Ile to trzy plus pięć?", "", compact=True)
        self.assertEqual(answer["memory_action"], "none")
        self.assertEqual(answer["memory_project_id"], "")
        self.assertEqual(answer["_ollama_timings"]["generation_s"], 1.5)
        self.assertEqual(answer["_ollama_timings"]["tokens_per_second"], 6)
        self.assertEqual(request.call_args.args[1]["format"]["required"], ["type", "text"])
        request.assert_called_once()

    def test_legacy_format_keeps_full_contract_and_rejects_missing_fields(self):
        legacy = {key: "" for key in b.LEGACY_SCHEMA["required"]}
        legacy.update(type="answer", text="Osiem.", memory_action="none")
        with patch.object(b, "make_request_json", return_value=raw(legacy)):
            self.assertEqual(b.call_ollama([], "Pytanie", "", compact=False)["text"], "Osiem.")
        with patch.object(b, "make_request_json", return_value=raw({"type": "answer", "text": "Osiem."})), \
             self.assertRaises(b.OllamaError):
            b.call_ollama([], "Pytanie", "", compact=False)

    def test_old_full_response_remains_accepted_in_compact_mode(self):
        legacy = {key: "" for key in b.LEGACY_SCHEMA["required"]}
        legacy.update(type="answer", text="Odpowiedź.", memory_action="none")
        with patch.object(b, "make_request_json", return_value=raw(legacy)):
            self.assertEqual(b.call_ollama([], "Pytanie", "", compact=True)["text"], "Odpowiedź.")

    def test_invalid_or_incomplete_action_is_rejected_without_retry(self):
        cases = [
            {"type": "answer", "text": " "}, {"type": "other", "text": "x"},
            {"type": "answer", "text": "x", "memory_action": "execute"},
            {"type": "answer", "text": "x", "memory_action": "add_fact"},
            {"type": "answer", "text": "x", "memory_action": "replace_fact", "memory_project_id": "project", "memory_value": "fact"},
            {"type": "answer", "text": "x", "memory_action": None},
            {"type": "answer", "text": "x", "extra": "unknown"},
        ]
        for item in cases:
            with self.subTest(item=item), patch.object(b, "make_request_json", return_value=raw(item)) as request, \
                 self.assertRaises(b.OllamaError):
                b.call_ollama([], "Pytanie", "", compact=True)
            request.assert_called_once()

    def test_truncated_response_is_not_accepted(self):
        with patch.object(b, "make_request_json", return_value=raw({"type": "answer", "text": "x"}, done_reason="length")), \
             self.assertRaises(b.OllamaError):
            b.call_ollama([], "Pytanie", "", compact=True)

    def test_rules_file_and_schema_are_not_mutated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config").mkdir()
            rules = root / "config/brain_rules.txt"
            original = (b.OLD_FORMAT_RULE + "\n- Moja własna reguła polszczyzny.\n").encode()
            rules.write_bytes(original)
            previous = copy.deepcopy(b.LEGACY_SCHEMA)
            with patch.object(b, "ROOT", root):
                compact = b.build_payload([], "Pytanie", "Projekt A", compact=True)
                legacy = b.build_payload([], "Pytanie", "Projekt A", compact=False)
            self.assertEqual(rules.read_bytes(), original)
            self.assertIn("Moja własna reguła", compact["messages"][0]["content"])
            self.assertNotIn(b.OLD_FORMAT_RULE, compact["messages"][0]["content"])
            self.assertIn(b.OLD_FORMAT_RULE, legacy["messages"][0]["content"])
            self.assertEqual(b.LEGACY_SCHEMA, previous)

    def test_history_note_and_runtime_options_are_preserved(self):
        history = [{"role": "user", "content": "Wcześniejsze pytanie"}]
        with patch.dict(os.environ, {"EVA_KEEP_ALIVE": "30m", "EVA_JSON_FORMAT": "compact"}):
            payload = b.build_payload(history, "Pytanie", "Pamięć", "Potwierdzony alias")
        self.assertEqual(payload["messages"][1:-1], history)
        self.assertIn("Potwierdzony alias", payload["messages"][0]["content"])
        self.assertEqual(payload["options"], {"temperature": 0.2, "num_ctx": 3072, "num_predict": 280})
        self.assertEqual(payload["keep_alive"], "30m")
        with patch.dict(os.environ, {"EVA_JSON_FORMAT": "wrong"}), self.assertRaises(b.OllamaError):
            b.build_payload([], "Pytanie", "")

    def test_missing_or_bad_metrics_do_not_invent_zero_times(self):
        self.assertEqual(b.ollama_timings({}), {})
        self.assertEqual(b.ollama_timings({"eval_duration": float("nan"), "load_duration": -1,
                                         "prompt_eval_duration": True, "eval_count": "9"}), {})
        self.assertEqual(b.ollama_timings({"eval_duration": 0, "eval_count": 0}),
                         {"generation_s": 0, "output_tokens": 0})

    def test_compact_memory_proposal_and_confirmation_survive_normalization(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            projects = root / "projects.json"
            projects.write_text(json.dumps({"schema_version": 1, "projects": [
                {"id": "smartroom", "name": "SmartRoom", "aliases": [], "facts": []}]}))
            brain = b.Brain(projects, root / "history.db")
            response = {"type": "answer", "text": "Rozumiem.", "memory_action": "add_fact",
                        "memory_project_id": "smartroom", "memory_value": "SmartRoom wykorzystuje czujnik ruchu."}
            with patch.dict(os.environ, EVA_JSON_FORMAT="compact"), \
                 patch.object(b, "make_request_json", return_value=raw(response)) as request:
                answer = brain.handle("Chyba SmartRoom wykorzystuje czujnik ruchu")
                self.assertEqual(answer["memory"], "pending_confirmation")
                self.assertEqual(answer["timings"]["output_tokens"], 9)
                answer = brain.handle("tak")
            request.assert_called_once()
            self.assertEqual(answer["memory"], "saved")
            self.assertEqual(brain.store.project("smartroom")["facts"], [response["memory_value"]])

    def test_compact_clarification_keeps_alias_and_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            projects = root / "projects.json"
            projects.write_text(json.dumps({"schema_version": 1, "projects": [
                {"id": "smartroom", "name": "SmartRoom", "aliases": [], "facts": []}]}))
            brain = b.Brain(projects, root / "history.db")
            first = {"type": "clarify", "text": "Czy chodzi o SmartRoom?", "interpretation": "SmartRoom",
                     "clarification_project_id": "smartroom", "clarification_alias": "small rum"}
            second = {"type": "answer", "text": "Nie mam statusu urządzenia."}
            with patch.dict(os.environ, EVA_JSON_FORMAT="compact"), \
                 patch.object(b, "make_request_json", side_effect=[raw(first), raw(second)]):
                self.assertEqual(brain.handle("Podaj stan small rum")["type"], "clarify")
                brain.handle("tak")
            self.assertEqual(brain.store.project("smartroom")["aliases"], ["small rum"])

    def test_benchmark_does_not_save_proposed_facts_or_change_live_files(self):
        path = b.ROOT.parents[1] / "instalation/benchmark_ai.py"
        spec = importlib.util.spec_from_file_location("benchmark_ai", path)
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        with tempfile.TemporaryDirectory() as folder:
            projects = Path(folder) / "projects.json"
            original = b'{"schema_version":1,"projects":[{"id":"test","name":"Test","aliases":[],"facts":[]}]}'
            projects.write_bytes(original)
            with patch.object(b, "PROJECTS_PATH", projects), \
                 patch.object(bench, "model_info"), \
                 patch.object(b, "call_ollama", return_value={"type": "answer", "text": "TEST",
                              "memory_action": "add_fact", "memory_value": "Nowy fakt"}) as call, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(bench.main(["--full"]), 0)
            self.assertEqual(projects.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [projects])
            self.assertEqual(call.call_count, 7)


if __name__ == "__main__":
    unittest.main()
