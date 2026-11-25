"""Prawdziwe HTTP między klientem i serwerem; Piper/ALSA zastąpione atrapami."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speech_output as speech
import voice_client as client

spec = importlib.util.spec_from_file_location("ewa_tts_server", client.ROOT.parent / "Eva-AI/EWA_TTS/server.py")
tts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tts)


class RemotePiper(unittest.TestCase):
    def setUp(self):
        self.calls = []
        owner = self

        class FakeVoice:
            def synthesize_wav(self, text, audio, syn_config):
                owner.calls.append((text, syn_config.length_scale))
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(22050)
                audio.writeframes(b"\0\0" * 2205)

        self.load = Mock(return_value=FakeVoice())
        module = types.SimpleNamespace(PiperVoice=types.SimpleNamespace(load=self.load),
                                       SynthesisConfig=types.SimpleNamespace)
        with patch.dict(sys.modules, piper=module):
            self.service = tts.PiperService(Path("gosia.onnx"))
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), tts.make_handler(self.service))
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.addCleanup(self.close_server)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "output.wav"

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)

    def request(self, payload=None, path="/tts"):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url + path, data=body,
                                         headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=2)

    def test_model_loads_once_and_handles_unicode_and_scale(self):
        self.assertEqual(self.calls, [("Cześć. Tu Ewa.", 1.0)])
        for text in ("Żółć i źdźbło.", "Dwa plus dwa to cztery."):
            speech.remote_piper(text, self.output, self.url, 1.2)
            self.assertAlmostEqual(speech.wav_duration(self.output), 0.1)
        self.load.assert_called_once_with("gosia.onnx")
        self.assertEqual(self.calls[-1], ("Dwa plus dwa to cztery.", 1.2))
        with self.request(path="/health") as response:
            health = json.load(response)
        self.assertEqual(health["status"], "ready")
        self.assertFalse(health["busy"])

    def test_invalid_requests_do_not_synthesize(self):
        payloads = [{}, [], {"text": " "}, {"text": "a" * 1501}, {"text": 7},
                    {"text": "a", "length_scale": True}, {"text": "a", "length_scale": float("nan")},
                    {"text": "a", "length_scale": 3}, {"text": "a", "model": "other"}]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(payload)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        self.assertEqual(len(self.calls), 1)

    def test_busy_does_not_queue_and_recovers(self):
        with self.service.lock:
            with self.assertRaisesRegex(speech.SpeechError, "503"):
                speech.remote_piper("Test", self.output, self.url, 1)
        speech.remote_piper("Test", self.output, self.url, 1)
        self.assertEqual(len(self.calls), 2)

    def test_synthesis_failure_releases_lock(self):
        with patch.object(self.service, "render", side_effect=RuntimeError("test")), \
             self.assertLogs(level="ERROR"), self.assertRaisesRegex(speech.SpeechError, "500"):
            speech.remote_piper("Test", self.output, self.url, 1)
        self.assertFalse(self.service.lock.locked())
        speech.remote_piper("Test", self.output, self.url, 1)

    def test_invalid_truncated_and_oversize_audio_rejected(self):
        valid = self.service.render("Test", 1)
        for data in (b"not a wav", valid[:-10]):
            with self.subTest(size=len(data)), patch.object(self.service, "render", return_value=data), \
                 self.assertRaises(speech.SpeechError):
                speech.remote_piper("Test", self.output, self.url, 1)
        with patch.object(speech, "MAX_WAV_BYTES", 100), self.assertRaises(speech.SpeechError):
            speech.remote_piper("Test", self.output, self.url, 1)

    def test_speak_needs_no_local_piper_and_playback_failure_does_not_retry(self):
        with patch.object(speech.shutil, "which", return_value="aplay"), \
             patch.object(speech, "piper_paths", side_effect=AssertionError("local Piper")), \
             patch.object(speech, "run_audio", side_effect=speech.SpeechError("ALSA")) as playback, \
             self.assertRaisesRegex(speech.SpeechError, "ALSA"):
            speech.speak("Test", engine="piper-remote", tts_server=self.url)
        self.assertEqual(len(self.calls), 2)  # warmup + one request
        playback.assert_called_once()
        self.assertEqual(playback.call_args.args[0][0], "aplay")

    def test_strict_remote_failure_never_calls_local_synthesizer(self):
        with self.service.lock, patch.object(speech.shutil, "which", return_value="aplay"), \
             patch.object(speech, "run_audio") as run, self.assertRaises(speech.SpeechError):
            speech.speak("Test", engine="piper-remote", tts_server=self.url, fallback=False)
        run.assert_not_called()

    def test_remote_failure_falls_back_to_espeak_once_without_repeating_ai(self):
        def audio(command, *args, **kwargs):
            if command[0] == "espeak-ng":
                path = command[command.index("-w") + 1]
                Path(path).write_bytes(self.service.render("Fallback", 1))
        args = client.parse_args(["--speak", "--tts-engine", "piper-remote", "--tts-server", self.url])
        with self.service.lock, patch.object(speech.shutil, "which", return_value="fake"), \
             patch.object(speech, "run_audio", side_effect=audio) as run, \
             patch.object(client, "ask_brain", return_value={"text": "Test"}) as brain, \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(client.handle_text("Pytanie", args))
        brain.assert_called_once()
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["espeak-ng", "aplay"])

    def test_remote_auto_keeps_vosk_for_two_turns(self):
        with patch.object(client, "load_vosk", return_value=("model", "factory")) as load, \
             patch.object(client, "transcribe_isolated", side_effect=AssertionError("isolated")), \
             patch.object(client, "transcribe_wav", return_value="Test") as recognize, \
             patch.object(client, "record_wav"), \
             patch.object(client, "check_server", return_value={"model": "ewa"}), \
             patch.object(client, "ask_brain", return_value={"text": "Odpowiedź"}) as brain, \
             patch.object(client.shutil, "which", return_value="fake"), \
             patch.object(speech, "run_audio") as run, \
             patch("builtins.input", side_effect=["", "", "q"]), \
             contextlib.redirect_stdout(io.StringIO()):
            result = client.main(["--speak", "--tts-engine", "piper-remote", "--tts-server", self.url])
        self.assertEqual(result, 0)
        load.assert_called_once()
        self.assertEqual(recognize.call_count, 2)
        self.assertEqual(brain.call_count, 2)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["aplay", "aplay"])

    def test_remote_config_and_invalid_urls(self):
        path = Path(self.temp.name) / "audio.json"
        path.write_text(json.dumps({"tts_engine": "piper-remote", "tts_server": self.url,
                                    "stt_mode": "auto", "device": "original"}))
        args = client.parse_args(["--config", str(path)])
        self.assertEqual(args.tts_server, self.url)
        self.assertEqual(args.device, "original")
        for url in ("file:///tmp/a", "http://a/tts", "http://a:99999", "http://a/#fragment", "http://u:p@a"):
            with self.subTest(url=url), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                client.parse_args(["--tts-server", url])


if __name__ == "__main__":
    unittest.main()
