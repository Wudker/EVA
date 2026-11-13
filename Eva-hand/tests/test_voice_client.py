"""Testy bez mikrofonu, Vosk i Ollamy; HTTP działa na lokalnym porcie testowym."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_client as client


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.reply = {"session_id": "ewa-voice", "type": "answer", "text": "Cześć, słucham!", "memory": "none"}
        self.code = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.received.append(("GET", self.path, None))
                self.respond({"status": "ok", "model": "ewa", "ollama": "online"})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.received.append(("POST", self.path, data))
                self.respond(owner.reply)

            def respond(self, data):
                body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(owner.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.addCleanup(self.close_server)

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(timeout=2)

    def test_unicode_and_same_session_across_two_questions(self):
        client.ask_brain(self.url, "Żółć, światło i pamięć", "ewa-test", 2)
        client.ask_brain(self.url, "tak", "ewa-test", 2)
        self.assertEqual([item[1] for item in self.received], ["/chat", "/chat"])
        self.assertEqual(self.received[0][2]["text"], "Żółć, światło i pamięć")
        self.assertEqual([item[2]["session_id"] for item in self.received], ["ewa-test", "ewa-test"])

    def test_blank_and_oversize_questions_never_leave_client(self):
        for text in (" ", "a" * 601):
            with self.subTest(text_length=len(text)), self.assertRaises(client.ClientError):
                client.ask_brain(self.url, text, "ewa-test", 2)
        self.assertEqual(self.received, [])

    def test_http_error_is_explained_and_not_retried(self):
        self.code = 502
        self.reply = {"error": "Ollama niedostępna"}
        with self.assertRaisesRegex(client.ClientError, "HTTP 502.*Ollama"):
            client.ask_brain(self.url, "Cześć", "ewa-test", 2)
        self.assertEqual(len(self.received), 1)

    def test_malformed_or_missing_answer_is_rejected(self):
        for reply in (b"<html>error</html>", [], {"error": "oops"}, {"text": " "}):
            self.reply = reply
            with self.subTest(reply=reply), self.assertRaises(client.ClientError):
                client.ask_brain(self.url, "Cześć", "ewa-test", 2)

    def test_health_and_text_cli_work_without_audio_dependencies(self):
        for extra in (["--check"], ["--text", "Kim jesteś?"]):
            result = subprocess.run(
                [sys.executable, str(client.ROOT / "voice_client.py"), "--server", self.url, *extra],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ewa" if extra[0] == "--check" else "Cześć, słucham!", result.stdout)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.wav = Path(self.directory.name) / "input.wav"

    def write_wav(self, channels=1, rate=16000):
        with wave.open(str(self.wav), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(b"\0\0" * 12000 * channels)

    def test_all_completed_segments_and_final_tail_are_preserved(self):
        self.write_wav()
        recognizer = Mock()
        recognizer.AcceptWaveform.side_effect = [True, False, True]
        recognizer.Result.side_effect = [json.dumps({"text": "ile to"}), json.dumps({"text": "dwa plus"})]
        recognizer.FinalResult.return_value = json.dumps({"text": "dwa"})
        factory = Mock(return_value=recognizer)
        self.assertEqual(client.transcribe_wav(self.wav, "model", factory), "ile to dwa plus dwa")
        factory.assert_called_once_with("model", 16000)

    def test_stereo_and_wrong_rate_rejected_before_stt(self):
        factory = Mock()
        for channels, rate in ((2, 16000), (1, 48000)):
            self.write_wav(channels, rate)
            with self.subTest(channels=channels, rate=rate), self.assertRaisesRegex(client.ClientError, "mono"):
                client.transcribe_wav(self.wav, None, factory)
        factory.assert_not_called()

    def test_silence_is_not_sent_to_ai(self):
        args = client.parse_args(["--once"])
        with patch.object(client, "ask_brain") as api, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(client.handle_text("  ", args))
        api.assert_not_called()

    def test_stt_only_never_calls_api(self):
        args = client.parse_args(["--stt-only"])
        with patch.object(client, "ask_brain") as api, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(client.handle_text("Cześć", args))
        api.assert_not_called()

    def test_record_failure_includes_alsa_error_and_reaps_process(self):
        process = Mock(returncode=1)
        process.communicate.return_value = (None, b"Unknown PCM test")
        process.poll.return_value = 1
        with patch.object(client.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(client.ClientError, "Unknown PCM"):
                client.record_wav(self.wav, "test", 1)
        process.wait.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_record_timeout_and_interrupt_release_device(self):
        for error in (subprocess.TimeoutExpired("arecord", 11), KeyboardInterrupt()):
            process = Mock()
            process.communicate.side_effect = error
            process.poll.return_value = None
            expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else client.ClientError
            with self.subTest(error=type(error).__name__), patch.object(client.subprocess, "Popen", return_value=process):
                with self.assertRaises(expected):
                    client.record_wav(self.wav, "default", 1)
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            process.stderr.close.assert_called_once()

    def test_no_recording_survives_before_http_request(self):
        recordings = []
        def record(path, device, seconds):
            recordings.append(path)
            path.write_bytes(b"temporary recording")
        def handle(text, args):
            self.assertEqual(text, "Test")
            self.assertFalse(recordings[0].exists())
            return True
        with patch.object(client, "check_server", return_value={"model": "ewa"}), \
             patch.object(client.shutil, "which", return_value="/usr/bin/arecord"), \
             patch.object(client, "load_vosk", return_value=(None, None)), \
             patch("builtins.input", return_value=""), \
             patch.object(client, "record_wav", side_effect=record), \
             patch.object(client, "transcribe_wav", return_value="Test"), \
             patch.object(client, "handle_text", side_effect=handle), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(client.main(["--once"]), 0)


class ArgumentTests(unittest.TestCase):
    def test_server_paths_and_invalid_limits_are_rejected(self):
        for args in (["--server", "http://10.77.77.1:8000/chat"], ["--seconds", "0"],
                     ["--seconds", "31"], ["--session", "źle"], ["--session", "x" * 33],
                     ["--timeout", "nan"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                client.parse_args(args)


if __name__ == "__main__":
    unittest.main()
