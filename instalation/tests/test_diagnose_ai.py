import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnose_ai as diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_only_ewa_includes_project_context_and_all_modes_share_options(self):
        payloads = [diagnostic.payload_for(mode, "Pytanie", "tajny-kontekst-testowy")
                    for mode in ("plain", "short-json", "ewa")]
        self.assertNotIn("format", payloads[0])
        self.assertEqual(payloads[1]["format"]["required"], ["type", "text"])
        for payload in payloads:
            self.assertEqual(payload["options"], payloads[0]["options"])
            self.assertEqual(payload["model"], payloads[0]["model"])
            self.assertEqual(len(payload["messages"]), 2)
            self.assertEqual(payload["messages"][-1], {"role": "user", "content": "Pytanie"})
        self.assertNotIn("tajny-kontekst-testowy", str(payloads[:2]))
        self.assertIn("tajny-kontekst-testowy", str(payloads[2]))

    def test_all_questions_are_direct_requests_without_writing_user_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            projects = Path(folder) / "projects.json"
            original = diagnostic.b.PROJECTS_PATH.with_name("projects.example.json").read_bytes()
            projects.write_bytes(original)
            db = Path(folder) / "conversations.db"
            db.write_bytes(b"untouched database")
            calls = []

            def request(url, payload, timeout):
                calls.append(payload)
                answer = "Osiem." if "format" not in payload else json.dumps({"type": "answer", "text": "Osiem."})
                return {"message": {"content": answer}, "done": True, "done_reason": "stop"}

            with patch.object(diagnostic.b, "PROJECTS_PATH", projects), \
                 patch.object(diagnostic.b, "DB_PATH", db), \
                 patch.object(diagnostic, "model_info"), \
                 patch.object(diagnostic.b, "make_request_json", side_effect=request), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(diagnostic.main([]), 0)
            self.assertEqual(len(calls), 9)
            self.assertEqual(projects.read_bytes(), original)
            self.assertEqual(db.read_bytes(), b"untouched database")

    def test_truncated_reply_is_reported_as_technical_failure(self):
        response = {"message": {"content": "Część odpowiedzi"}, "done": True, "done_reason": "length"}
        with patch.object(diagnostic, "model_info"), \
             patch.object(diagnostic.b, "make_request_json", return_value=response), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnostic.main(["--mode", "plain"]), 1)
        self.assertIn("ucięta", output.getvalue())


if __name__ == "__main__":
    unittest.main()
