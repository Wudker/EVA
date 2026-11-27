"""Symulacja odpowiedzi sterownika: potwierdzenia, dwa obwody, jasność względna."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.smartroom import MQTTLights, DEVICES, AVAILABILITY


class Client:
    def __init__(self, acknowledge=True, retained=False, fail_target=None):
        self.published = []
        self.acknowledge = acknowledge
        self.retained = retained
        self.fail_target = fail_target

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, *args, **kwargs):
        pass

    def subscribe(self, topics):
        self.topics = topics

    def message(self, topic, value, retained=True):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=str(value).encode(), retain=retained))

    def loop_start(self):
        self.on_connect(self, None, {}, 0)
        self.on_subscribe(self, None, 1, [0] * len(self.topics))
        self.message(AVAILABILITY, "online")
        for target, (_, prefix) in DEVICES.items():
            self.message(prefix + "/state", 0)
            self.message(prefix + "/brightness/state", 128 if target == "bed" else 64)

    def publish(self, topic, value, **kwargs):
        self.published.append((topic, value, kwargs))
        if self.fail_target and DEVICES[self.fail_target][1] in topic:
            return SimpleNamespace(rc=1)
        if self.acknowledge:
            self.message(topic.removesuffix("/set") + "/state", value, self.retained)
        return SimpleNamespace(rc=0)

    def disconnect(self):
        pass

    def loop_stop(self):
        pass


class MQTTTests(unittest.TestCase):
    def create(self, **kwargs):
        client = Client(**kwargs)
        return MQTTLights({"host": "unused"}, client=client, timeout=0.001), client

    def test_all_publishes_to_both_real_topics_without_retaining_commands(self):
        lights, client = self.create()
        result = lights.execute({"action": "power", "target": "all", "value": 1})
        self.assertEqual(result["device_status"], "confirmed")
        self.assertEqual(set(result["devices"]), {"bed", "shelf"})
        self.assertEqual({topic for topic, _, _ in client.published}, {prefix + "/set" for _, prefix in DEVICES.values()})
        self.assertTrue(all(options == {"qos": 0, "retain": False} for _, _, options in client.published))

    def test_relative_brightness_uses_each_devices_own_reported_value(self):
        lights, client = self.create()
        result = lights.execute({"action": "adjust_brightness", "target": "all", "value": 10})
        self.assertEqual(result["device_status"], "confirmed")
        self.assertEqual([int(value) for _, value, _ in client.published], [154, 90])

    def test_unknown_brightness_is_not_guessed(self):
        lights, client = self.create()
        lights.states.pop(DEVICES["bed"][1] + "/brightness/state")
        result = lights.execute({"action": "adjust_brightness", "target": "bed", "value": -10})
        self.assertEqual(result["device_status"], "unknown")
        self.assertFalse(client.published)

    def test_brightness_clamps_only_calculated_relative_result(self):
        lights, client = self.create()
        lights.execute({"action": "adjust_brightness", "target": "bed", "value": 100})
        self.assertEqual(client.published[-1][1], "255")
        lights.execute({"action": "adjust_brightness", "target": "bed", "value": -100})
        self.assertEqual(client.published[-1][1], "0")

    def test_reading_status_does_not_publish_any_command(self):
        lights, client = self.create()
        self.assertEqual(lights.execute({"action": "status", "target": "all"})["device_status"], "reported")
        self.assertFalse(client.published)

    def test_retained_echo_is_not_a_fresh_confirmation(self):
        lights, client = self.create(retained=True)
        result = lights.execute({"action": "power", "target": "bed", "value": 1})
        self.assertEqual(result["device_status"], "sent_unconfirmed")

    def test_partial_failure_is_visible_per_device(self):
        lights, client = self.create(fail_target="bed")
        result = lights.execute({"action": "power", "target": "all", "value": 1})
        self.assertEqual(result["device_status"], "partial")
        self.assertEqual(result["devices"]["bed"]["device_status"], "failed")
        self.assertEqual(result["devices"]["shelf"]["device_status"], "confirmed")

    def test_offline_does_not_publish_and_same_setting_is_reported_honestly(self):
        lights, client = self.create()
        result = lights.execute({"action": "power", "target": "bed", "value": 0})
        self.assertEqual(result["device_status"], "reported")
        client.message(AVAILABILITY, "offline")
        self.assertEqual(lights.execute({"action": "power", "target": "all", "value": 1})["device_status"], "offline")
        self.assertFalse(client.published)

    def test_invalid_command_never_reaches_publish(self):
        lights, client = self.create()
        for command in ({"action": "brightness", "target": "bed", "value": 300},
                        {"action": "power", "target": "bed", "value": True},
                        {"action": "power", "target": "kitchen", "value": 1}):
            with self.subTest(command=command), self.assertRaises(ValueError):
                lights.execute(command)
        self.assertFalse(client.published)


if __name__ == "__main__":
    unittest.main()
