"""Testy mowy z procesami zastępującymi ALSA/eSpeak; nie wymagają sprzętu."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_output as speech
import voice_client as client


class SpeechIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "spoken.json"
        script = f'''#!{sys.executable}
import json, os, sys, wave
from pathlib import Path
if Path(sys.argv[0]).name == "espeak-ng":
    text = sys.stdin.buffer.read().decode("utf-8")
    output = sys.argv[sys.argv.index("-w") + 1]
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\\0\\0" * 1600)
    Path(os.environ["EWA_TEST_LOG"]).write_text(json.dumps({{"text": text, "wav": output}}))
else:
    log = Path(os.environ["EWA_TEST_LOG"])
    data = json.loads(log.read_text())
    data["played_exists"] = Path(sys.argv[-1]).is_file()
    data["device"] = sys.argv[sys.argv.index("-D") + 1]
    log.write_text(json.dumps(data))
    if data["device"] == "bad":
        print("Unknown PCM bad", file=sys.stderr)
        sys.exit(2)
'''
        for name in ("espeak-ng", "aplay"):
            path = self.root / name
            path.write_text(script)
            path.chmod(0o755)
        self.env = patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
                                         "EWA_TEST_LOG": str(self.log)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unicode_and_shell_syntax_are_only_data_and_wav_is_removed(self):
        marker = self.root / "should-not-exist"
        text = f"Żółć i pamięć. $(touch {marker}) ; --version"
        speech.speak(text, "plughw:CARD=ReSpeaker,DEV=0")
        data = json.loads(self.log.read_text())
        self.assertEqual(data["text"], text)
        self.assertTrue(data["played_exists"])
        self.assertEqual(data["device"], "plughw:CARD=ReSpeaker,DEV=0")
        self.assertFalse(Path(data["wav"]).exists())
        self.assertFalse(marker.exists())

    def test_speaker_failure_keeps_answer_and_does_not_retry_chat(self):
        args = client.parse_args(["--speak", "--output-device", "bad"])
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(client, "ask_brain", return_value={"text": "Dwa plus dwa to cztery."}) as api, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertFalse(client.handle_text("Ile to dwa plus dwa?", args))
        api.assert_called_once()
        self.assertIn("Dwa plus dwa to cztery", output.getvalue())
        self.assertIn("Unknown PCM bad", errors.getvalue())
        self.assertFalse(Path(json.loads(self.log.read_text())["wav"]).exists())

    def test_tts_test_runs_without_vosk_or_server(self):
        with patch.object(client, "load_vosk") as vosk, patch.object(client, "check_server") as health, \
             patch.object(client, "ask_brain") as api:
            self.assertEqual(client.main(["--tts-test"]), 0)
        vosk.assert_not_called()
        health.assert_not_called()
        api.assert_not_called()

    def test_stt_only_with_speak_does_not_use_speaker(self):
        args = client.parse_args(["--stt-only", "--speak"])
        with patch.object(client, "speak") as voice, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(client.handle_text("test", args))
        voice.assert_not_called()


class SpeechFailureTests(unittest.TestCase):
    def test_timeout_and_interrupt_kill_and_reap_process(self):
        for failure in (subprocess.TimeoutExpired("aplay", 1), KeyboardInterrupt()):
            process = Mock()
            process.communicate.side_effect = failure
            process.poll.return_value = None
            expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else speech.SpeechError
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(speech.subprocess, "Popen", return_value=process), self.assertRaises(expected):
                speech.run_audio(["aplay", "test.wav"], 1)
            process.kill.assert_called_once()
            process.wait.assert_called_once()
            process.stderr.close.assert_called_once()

    def test_missing_tts_is_reported_before_request(self):
        with patch.object(client, "check_dependencies", side_effect=speech.SpeechError("Brak espeak-ng")), \
             patch.object(client, "ask_brain") as api, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(client.main(["--text", "Cześć", "--speak"]), 1)
        api.assert_not_called()

    def test_microphone_is_closed_before_speech_and_next_prompt(self):
        events = []
        def record(*args): events.append("record-closed")
        def play(*args, **kwargs): events.append("speech-finished"); return 0.1, 0.2
        with patch.object(client, "check_dependencies"), \
             patch.object(client, "check_server", return_value={"model": "ewa"}), \
             patch.object(client.shutil, "which", return_value="/usr/bin/arecord"), \
             patch.object(client, "load_vosk", return_value=(None, None)), \
             patch("builtins.input", return_value=""), \
             patch.object(client, "record_wav", side_effect=record), \
             patch.object(client, "transcribe_wav", return_value="Test"), \
             patch.object(client, "ask_brain", return_value={"text": "Odpowiedź"}), \
             patch.object(client, "speak", side_effect=play), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(client.main(["--once", "--speak"]), 0)
        self.assertEqual(events, ["record-closed", "speech-finished"])


class ConfigTests(unittest.TestCase):
    def test_cli_overrides_local_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"speak": True, "seconds": 12, "output_device": "my-card"}))
            args = client.parse_args(["--config", str(path), "--no-speak", "--seconds", "3"])
            self.assertFalse(args.speak)
            self.assertEqual(args.seconds, 3)
            self.assertEqual(args.output_device, "my-card")

    def test_invalid_configuration_fails_before_work(self):
        for config in ({"speak": "false"}, {"seconds": True}, {"seconds": 100}, {"command": "x"}, []):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                path.write_text(json.dumps(config))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    client.parse_args(["--config", str(path)])


if __name__ == "__main__":
    unittest.main()
