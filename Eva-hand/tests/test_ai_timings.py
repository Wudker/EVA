import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_client as client


class AITimings(unittest.TestCase):
    def test_new_metrics_are_shown_but_only_answer_is_spoken(self):
        args = client.parse_args(["--text", "Pytanie", "--speak"])
        answer = {"text": "Osiem.", "timings": {"load_s": 0, "prompt_s": 0.5,
                  "generation_s": 3.5, "output_tokens": 20}}
        with patch.object(client, "ask_brain", return_value=answer) as api, \
             patch.object(client, "say") as speaker, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(client.handle_text("Pytanie", args))
        api.assert_called_once()
        speaker.assert_called_once_with("Osiem.", args)
        self.assertIn("generowanie: 3.50 s", output.getvalue())
        self.assertIn("tokeny całego JSON: 20", output.getvalue())

    def test_old_or_malformed_metrics_do_not_break_client(self):
        for answer in ({"text": "x"}, {"timings": None}, {"timings": "bad"},
                       {"timings": {"load_s": "bad", "prompt_s": float("nan"), "output_tokens": True}}):
            with self.subTest(answer=answer), contextlib.redirect_stdout(io.StringIO()) as output:
                client.show_ai_timings(answer)
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
