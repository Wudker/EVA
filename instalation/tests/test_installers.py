import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class InstallerTests(unittest.TestCase):
    def test_shell_syntax(self):
        for path in (ROOT / "instalation").glob("*.sh"):
            with self.subTest(script=path.name):
                result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_runs_work_without_hardware_and_do_not_create_modelfile(self):
        modelfile = ROOT / "Eva-AI/EWA_Brain/config/Modelfile"
        old_data = modelfile.read_bytes() if modelfile.exists() else None
        cases = [("install_pi5.sh", []), ("install_pi5.sh", ["--usb-interface", "usb0"]),
                 ("install_pi3.sh", []), ("install_pi3.sh", ["--tts-only"]),
                 ("configure_usb.sh", ["--role", "pi3", "--interface", "usb0"])]
        for script, args in cases:
            with self.subTest(script=script, args=args):
                result = subprocess.run(["bash", str(ROOT / "instalation" / script), *args, "--dry-run"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("[plan]", result.stdout)
        self.assertEqual(modelfile.read_bytes() if modelfile.exists() else None, old_data)

    def test_unknown_options_and_wifi_interface_rejected(self):
        for script, args in (("install_pi5.sh", ["--unknown"]),
                             ("configure_usb.sh", ["--role", "pi5", "--interface", "wlan0", "--dry-run"])):
            result = subprocess.run(["bash", str(ROOT / "instalation" / script), *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
