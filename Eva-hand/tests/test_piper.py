"""Procesy testowe zamiast Pipera/ALSA/Vosk; sprawdzają integrację, nie brzmienie."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_output as speech
import voice_client as client


class PiperProcesses(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "log.jsonl"
        self.model = self.root / "gosia.onnx"
        self.model.touch()
        Path(str(self.model) + ".json").write_text("{}")
        self.python = self.root / "python"
        code = '''import json, os, sys, wave
from pathlib import Path
kind = Path(sys.argv[0]).name
text = sys.stdin.buffer.read().decode("utf-8") if kind != "aplay" else ""
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"kind": kind, "text": text, "args": sys.argv[1:]}) + "\\n")
if kind == "python":
    marker = Path(os.environ["FAKE_STT_PID"])
    if marker.exists() and Path("/proc/" + marker.read_text()).exists():
        sys.exit("Vosk still alive")
    output = Path(sys.argv[sys.argv.index("-f") + 1])
    mode = os.environ.get("FAKE_PIPER", "ok")
    if mode == "fail": sys.exit("Piper failure")
    if mode == "invalid": output.write_bytes(b"invalid"); sys.exit(0)
elif kind == "espeak-ng":
    output = Path(sys.argv[sys.argv.index("-w") + 1])
else:
    if "bad" in sys.argv: sys.exit("Unknown PCM bad")
    assert Path(sys.argv[-1]).exists()
    sys.exit(0)
with wave.open(str(output), "wb") as wav:
    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
    wav.writeframes(b"\\0\\0" * 2205)
'''
        for name in ("python", "espeak-ng", "aplay"):
            path = self.root / name
            path.write_text(f"#!{sys.executable}\n" + code)
            path.chmod(0o755)
        self.pid = self.root / "stt.pid"
        env = patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                     "FAKE_LOG": str(self.log), "FAKE_STT_PID": str(self.pid),
                                     "FAKE_PIPER": "ok"})
        env.start()
        self.addCleanup(env.stop)

    def kwargs(self, **overrides):
        return dict(engine="piper", piper_python=str(self.python), piper_model=str(self.model), **overrides)

    def entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_unicode_multiline_text_is_stdin_not_shell_or_cli(self):
        marker = self.root / "injection"
        text = f"Żółć.\n$(touch {marker}) --help ; `id`"
        speech.speak(text, "my-card", **self.kwargs())
        entries = self.entries()
        self.assertEqual([x["kind"] for x in entries], ["python", "aplay"])
        self.assertEqual(entries[0]["text"], text)
        self.assertNotIn(text, entries[0]["args"])
        self.assertFalse(marker.exists())
        self.assertFalse(Path(entries[1]["args"][-1]).exists())

    def test_synthesis_failure_or_invalid_wav_uses_fallback_once(self):
        for mode in ("fail", "invalid"):
            self.log.unlink(missing_ok=True)
            with self.subTest(mode=mode), patch.dict(os.environ, FAKE_PIPER=mode), \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                speech.speak("Cześć", **self.kwargs())
            self.assertEqual([x["kind"] for x in self.entries()], ["python", "espeak-ng", "aplay"])
            self.assertIn("awaryjnie", errors.getvalue())

    def test_strict_mode_does_not_fallback(self):
        with patch.dict(os.environ, FAKE_PIPER="fail"), self.assertRaises(speech.SpeechError):
            speech.speak("Cześć", **self.kwargs(fallback=False))
        self.assertEqual([x["kind"] for x in self.entries()], ["python"])

    def test_aplay_error_does_not_repeat_synthesis(self):
        with self.assertRaisesRegex(speech.SpeechError, "Unknown PCM"):
            speech.speak("Cześć", "bad", **self.kwargs())
        self.assertEqual([x["kind"] for x in self.entries()], ["python", "aplay"])

    def test_missing_model_uses_espeak_but_strict_mode_rejects_it(self):
        self.model.unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            speech.speak("Cześć", **self.kwargs())
        self.assertEqual([x["kind"] for x in self.entries()], ["espeak-ng", "aplay"])
        with self.assertRaises(speech.SpeechError):
            speech.speak("Cześć", **self.kwargs(fallback=False))

    def test_venv_python_symlink_is_preserved(self):
        link = self.root / "venv-python"
        link.symlink_to(sys.executable)
        executable, _ = speech.piper_paths(link, self.model)
        self.assertEqual(executable, link)
        self.assertNotEqual(executable, link.resolve())

    def test_real_worker_exits_before_api_and_piper_and_no_parent_vosk_load(self):
        stt_model = self.root / "vosk-model"
        (stt_model / "am").mkdir(parents=True)
        (stt_model / "am/final.mdl").touch()
        (self.root / "vosk.py").write_text('''import os
from pathlib import Path
def SetLogLevel(level): pass
class Model:
    def __init__(self, path): Path(os.environ["FAKE_STT_PID"]).write_text(str(os.getpid()))
class KaldiRecognizer:
    def __init__(self, model, rate): pass
    def AcceptWaveform(self, data): return False
    def FinalResult(self): return '{"text": "ile to dwa plus dwa"}'
''')
        def record(path, device, seconds):
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 1600)
        def answer(*args):
            self.assertTrue(self.pid.exists())
            self.assertFalse(Path("/proc/" + self.pid.read_text()).exists())
            return {"text": "Cztery."}
        argv = ["--once", "--speak", "--tts-engine", "piper", "--model", str(stt_model),
                "--piper-python", str(self.python), "--piper-model", str(self.model), "--audio-diagnostics"]
        with patch.dict(os.environ, PYTHONPATH=str(self.root)), \
             patch.object(client, "load_vosk") as persistent, \
             patch.object(client, "check_server", return_value={"model": "ewa"}), \
             patch.object(client, "record_wav", side_effect=record), \
             patch.object(client, "ask_brain", side_effect=answer) as api, \
             patch.object(client.shutil, "which", return_value="fake"), \
             patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(client.main(argv), 0)
        persistent.assert_not_called()
        api.assert_called_once()
        self.assertIn("pamięć Vosk", output.getvalue())
        self.assertIn("pamięć Piper", output.getvalue())
        self.assertEqual([x["kind"] for x in self.entries()], ["python", "aplay"])

    def test_fallback_never_repeats_api(self):
        args = client.parse_args(["--speak", "--tts-engine", "piper", "--piper-python", str(self.python),
                                  "--piper-model", str(self.model)])
        with patch.dict(os.environ, FAKE_PIPER="fail"), \
             patch.object(client, "ask_brain", return_value={"text": "Odpowiedź"}) as api, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(client.handle_text("Pytanie", args))
        api.assert_called_once()

    def test_cancelled_piper_never_falls_back(self):
        with patch.object(speech, "run_audio", side_effect=KeyboardInterrupt) as run, \
             self.assertRaises(KeyboardInterrupt):
            speech.speak("Cześć", **self.kwargs())
        run.assert_called_once()

    def test_worker_timeout_reaps_actual_child(self):
        marker = self.root / "pid"
        command = [sys.executable, "-c", "import os,time,pathlib; "
                   f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"]
        with self.assertRaises(speech.SpeechError):
            speech.run_audio(command, 0.3, capture=True)
        self.assertTrue(marker.exists())
        self.assertFalse(Path("/proc/" + marker.read_text()).exists())


class PiperConfig(unittest.TestCase):
    def test_new_config_defaults_and_cli_override(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.json"
            path.write_text(json.dumps({"tts_engine": "piper", "stt_mode": "isolated", "tts_fallback": False,
                                        "piper_length_scale": 1, "audio_diagnostics": True}))
            args = client.parse_args(["--config", str(path), "--tts-engine", "espeak", "--tts-fallback"])
            self.assertEqual(args.tts_engine, "espeak")
            self.assertTrue(args.tts_fallback)
            self.assertTrue(args.audio_diagnostics)

    def test_invalid_new_config_rejected(self):
        for data in ({"tts_engine": "other"}, {"stt_mode": "bad"}, {"piper_length_scale": float("nan")},
                     {"piper_length_scale": True}, {"tts_fallback": "false"}, {"piper_python": "\n"}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "audio.json"
                path.write_text(json.dumps(data))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    client.parse_args(["--config", str(path)])

    def test_configuration_preserves_settings_private_backup_and_is_idempotent(self):
        spec = importlib.util.spec_from_file_location("configure_piper", client.ROOT.parent / "instalation/configure_piper.py")
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audio.local.json"
            original = b'{"device":"original","seconds":12,"speech_rate":145}'
            path.write_bytes(original)
            backups = Path(folder) / "backups"
            backup, changed = config_module.configure(path, {"tts_engine": "piper"}, backups)
            self.assertTrue(changed)
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["seconds"], 12)
            self.assertEqual(json.loads(path.read_text())["device"], "original")
            self.assertEqual(config_module.configure(path, {"tts_engine": "piper"}, backups), (None, False))
            before = path.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                config_module.configure(path, {"seconds": 999}, backups)
            self.assertEqual(path.read_bytes(), before)

    def test_memory_samples_track_peak_and_minima(self):
        monitor = speech.MemorySamples(999999, "test")
        with patch.object(speech, "_proc_values", side_effect=[
            {"VmRSS": 100}, {"MemAvailable": 400, "SwapFree": 200},
            {"VmRSS": 300}, {"MemAvailable": 250, "SwapFree": 170},
            {"VmRSS": 50}, {"MemAvailable": 500, "SwapFree": 190}]):
            for _ in range(3): monitor._sample()
        self.assertEqual((monitor.rss, monitor.available, monitor.swap_free), (300, 250, 170))


if __name__ == "__main__":
    unittest.main()
