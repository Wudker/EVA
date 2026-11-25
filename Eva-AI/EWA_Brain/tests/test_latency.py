"""Rozgrzewanie bez skutków ubocznych i rzeczywisty HTTP z atrapą Ollamy."""
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from modules import brain as b


class LatencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.projects = self.folder / "projects.json"
        self.projects.write_text(json.dumps({"schema_version": 1, "projects": [
            {"id": "smartroom", "name": "SmartRoom", "aliases": [], "facts": []}]}))
        self.brain = b.Brain(self.projects, self.folder / "history.db")

    def test_warmup_is_once_and_does_not_save_even_a_memory_proposal(self):
        original = self.projects.read_bytes()
        with closing(b.db_connect(self.brain.db_path)) as db:
            b.save_turn(db, "voice", "Stare pytanie", "Stara odpowiedź")
            b.set_pending(db, "voice", "memory", {"action": "add_fact"})
        client = Mock(return_value={"type": "answer", "text": "Zapamiętam.",
                      "memory_action": "add_fact", "memory_project_id": "smartroom",
                      "memory_value": "Czujnik temperatury"})
        self.brain.model_client = client
        self.brain.warmup()
        self.brain.warmup()
        client.assert_called_once()
        self.assertEqual(client.call_args.args[0], [])
        self.assertEqual(self.brain.warmup_state, "ready")
        self.assertEqual(self.projects.read_bytes(), original)
        with closing(b.db_connect(self.brain.db_path)) as db:
            self.assertEqual(len(b.recent(db, "voice")), 2)
            self.assertEqual(b.get_pending(db, "voice"), ("memory", {"action": "add_fact"}))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_warmup_failure_allows_later_chat(self):
        self.brain.model_client = Mock(side_effect=[b.OllamaError("offline"),
                                      {"type": "answer", "text": "Już działam."}])
        with self.assertLogs(level="ERROR"):
            self.brain.warmup()
        self.assertEqual(self.brain.warmup_state, "error")
        self.assertEqual(self.brain.handle("Cześć")["text"], "Już działam.")

    def test_warmup_and_user_chat_do_not_run_model_in_parallel(self):
        entered = threading.Event()
        release = threading.Event()
        chat_started = threading.Event()
        chat_finished = threading.Event()
        result = []

        def model(history, text, context, note=""):
            if text == "Cześć.":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test timeout")
            return {"type": "answer", "text": "Cześć."}

        def chat():
            chat_started.set()
            result.append(self.brain.handle("Przywitaj się."))
            chat_finished.set()

        self.brain.model_client = model
        warm = threading.Thread(target=self.brain.warmup)
        user = threading.Thread(target=chat)
        warm.start()
        try:
            self.assertTrue(entered.wait(2))
            user.start()
            self.assertTrue(chat_started.wait(2))
            self.assertFalse(chat_finished.wait(0.03))
            with patch.object(b, "make_request_json", return_value={"models": [{"name": b.MODEL}]}):
                self.assertEqual(self.brain.health()["warmup"], "running")
        finally:
            release.set()
            warm.join(3)
            if user.ident is not None:
                user.join(3)
        self.assertFalse(warm.is_alive())
        self.assertFalse(user.is_alive())
        self.assertEqual(result[0]["text"], "Cześć.")

    def test_health_requires_the_configured_model_tag(self):
        with patch.object(b, "MODEL", "ewa:fast"), patch.object(b, "make_request_json",
                          return_value={"models": [{"name": "ewa:latest"}]}):
            self.assertEqual(self.brain.health()["status"], "model_missing")
        with patch.object(b, "MODEL", "ewa"), patch.object(b, "make_request_json",
                          return_value={"models": [{"name": "ewa:latest"}]}):
            self.assertEqual(self.brain.health()["status"], "ok")

    def test_oversized_memory_value_is_rejected_instead_of_truncated(self):
        raw = {"done": True, "message": {"content": json.dumps({"type": "answer", "text": "x",
               "memory_action": "add_fact", "memory_project_id": "smartroom", "memory_value": "x" * 241})}}
        with patch.object(b, "make_request_json", return_value=raw), self.assertRaises(b.OllamaError):
            b.call_ollama([], "Fakt", "")

    def test_http_round_trip_returns_timings_and_handles_model_error(self):
        spec = importlib.util.spec_from_file_location("ewa_test_server", b.ROOT / "server.py")
        server_module = importlib.util.module_from_spec(spec)
        with patch.object(b, "Brain", return_value=self.brain):
            spec.loader.exec_module(server_module)
        server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = "http://127.0.0.1:" + str(server.server_port)
        request = urllib.request.Request(url + "/chat", data=json.dumps({
            "text": "Przywitaj się.", "session_id": "test"}).encode(),
            headers={"Content-Type": "application/json"})
        raw = {"done": True, "done_reason": "stop", "message": {"content":
               '{"type":"answer","text":"Cześć."}'}, "eval_count": 12, "eval_duration": 2_000_000_000}
        try:
            with patch.object(b, "make_request_json", return_value=raw) as ollama, \
                 patch.object(server_module.Handler, "log_message"):
                with opener.open(request, timeout=2) as response:
                    result = json.load(response)
                    self.assertEqual(response.status, 200)
                self.assertEqual(result["text"], "Cześć.")
                self.assertEqual(result["timings"]["generation_s"], 2)
                self.assertEqual(ollama.call_args.args[1]["format"]["required"], ["type", "text"])
                ollama.assert_called_once()
            with patch.object(b, "make_request_json", side_effect=b.OllamaError("offline")), \
                 patch.object(server_module.Handler, "log_message"), self.assertLogs(level="WARNING"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(request, timeout=2)
                with caught.exception as response:
                    self.assertEqual(response.code, 502)
                    self.assertIn("error", json.load(response))
        finally:
            server.shutdown()
            thread.join(3)
            server.server_close()
        with closing(b.db_connect(self.brain.db_path)) as db:
            self.assertEqual(len(b.recent(db, "test")), 2)

    def test_comparison_warms_each_format_and_does_not_require_live_projects(self):
        path = b.ROOT.parents[1] / "instalation/benchmark_ai.py"
        spec = importlib.util.spec_from_file_location("ewa_benchmark_latency", path)
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        self.projects.rename(self.folder / "projects.example.json")
        with patch.object(b, "PROJECTS_PATH", self.projects), patch.object(bench, "model_info"), \
             patch.object(b, "call_ollama", return_value={"type": "answer", "text": "x",
                          "memory_action": "none"}) as client, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bench.main(["--compare", "--repeat", "2"]), 0)
        self.assertFalse(self.projects.exists())
        self.assertEqual(client.call_count, 14)  # 2 rozgrzewania + 2 formaty * 3 pytania * 2
        self.assertEqual(client.call_args_list[0].args[1], "Cześć.")
        self.assertEqual(client.call_args_list[7].args[1], "Cześć.")
        self.assertFalse(client.call_args_list[0].kwargs["compact"])
        self.assertTrue(client.call_args_list[7].kwargs["compact"])
        self.assertIn("mediana", output.getvalue())


if __name__ == "__main__":
    unittest.main()
