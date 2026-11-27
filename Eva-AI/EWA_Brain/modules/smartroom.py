"""Sterowanie obwodami LED SmartRoom i odczyt ich rzeczywistego stanu z MQTT."""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid

from .light_commands import normalize, parse_command, target_from, raw_brightness

AVAILABILITY = "smartroom/workshop/led_controller/availability"
DEVICES = {"bed": ("przy łóżku", "smartroom/workshop/led_bed"),
           "shelf": ("na półce", "smartroom/workshop/led_shelf")}
STATE_TOPICS = [AVAILABILITY, *[prefix + suffix for _, prefix in DEVICES.values()
                               for suffix in ("/state", "/brightness/state")]]


def read_config(path):
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(data)


def validate_config(data):
    if not isinstance(data, dict) or set(data) - {"host", "port", "username", "password"}:
        raise ValueError("Niepoprawny plik mqtt.local.json")
    host = data.get("host", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", host):
        raise ValueError("Podaj adres IP lub nazwę istniejącego brokera MQTT")
    port = data.get("port", 1883)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Niepoprawny port MQTT")
    for key in ("username", "password"):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
            raise ValueError("Niepoprawne dane logowania MQTT")
    if data.get("password") and not data.get("username"):
        raise ValueError("Hasło MQTT wymaga nazwy użytkownika")
    return {"host": host, "port": port, "username": data.get("username", ""), "password": data.get("password", "")}


class DisabledLights:
    enabled = False

    def __init__(self, message="Sterowanie LED nie jest skonfigurowane. Uruchom konfigurację MQTT na Pi 5."):
        self.message = message

    def execute(self, command):
        return {"type": "answer", "text": self.message, "source": "mqtt", "device_status": "unavailable"}


def mqtt_client():
    import paho.mqtt.client as mqtt
    options = {"client_id": "ewa-led-" + uuid.uuid4().hex[:12], "clean_session": True}
    if hasattr(mqtt, "CallbackAPIVersion"):
        options["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    return mqtt.Client(**options)


class MQTTLights:
    enabled = True

    def __init__(self, config, client=None, timeout=2.0):
        self.client = client if client is not None else mqtt_client()
        self.timeout = timeout
        self.command_lock = threading.Lock()
        self.condition = threading.Condition()
        self.connected = False
        self.subscribed = False
        self.availability = None
        self.states = {}
        self.sequence = 0
        if config.get("username"):
            self.client.username_pw_set(config["username"], config.get("password", ""))
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.connect_async(config["host"], config.get("port", 1883), keepalive=30)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, reason, properties=None):
        with self.condition:
            self.connected = reason == 0
            self.subscribed = False
            self.availability = None
            self.states.clear()
            if self.connected:
                client.subscribe([(topic, 0) for topic in STATE_TOPICS])
            self.condition.notify_all()

    def on_subscribe(self, client, userdata, mid, reasons, properties=None):
        with self.condition:
            self.subscribed = bool(reasons) and all(not getattr(reason, "is_failure", False)
                                                   and (not isinstance(reason, int) or reason < 128)
                                                   for reason in reasons)
            self.condition.notify_all()

    def on_disconnect(self, client, userdata, *args):
        with self.condition:
            self.connected = self.subscribed = False
            self.availability = None
            self.states.clear()
            self.condition.notify_all()

    def on_message(self, client, userdata, message):
        if message.topic not in STATE_TOPICS or len(message.payload) > 16:
            return
        try:
            value = message.payload.decode("ascii")
        except UnicodeError:
            return
        with self.condition:
            if message.topic == AVAILABILITY:
                if value not in ("online", "offline"):
                    return
                self.availability = value
                if value == "offline":
                    self.states.clear()
            else:
                maximum = 255 if "/brightness/" in message.topic else 1
                if not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > maximum:
                    return
                self.sequence += 1
                self.states[message.topic] = (int(value), self.sequence, bool(message.retain))
            self.condition.notify_all()

    def execute(self, command):
        target, action, value = command["target"], command["action"], command.get("value")
        minimum, maximum = (-100, 100) if action == "adjust_brightness" else (0, 1 if action == "power" else 255)
        if (target not in (*DEVICES, "all") or action not in ("power", "brightness", "adjust_brightness", "status") or
                (action != "status" and (type(value) is not int or not minimum <= value <= maximum))):
            raise ValueError("Polecenie spoza listy dozwolonych działań LED")
        # Każdy obwód ma osobny temat i własne potwierdzenie; brak wspólnej transakcji w ESP32.
        with self.command_lock:
            if target != "all":
                return self._execute_one(target, action, value)
            results = {key: self._execute_one(key, action, value) for key in DEVICES}
        statuses = {result["device_status"] for result in results.values()}
        return {"type": "answer", "text": " ".join(result["text"] for result in results.values()),
                "source": "mqtt", "device_status": next(iter(statuses)) if len(statuses) == 1 else "partial",
                "devices": results}

    def _execute_one(self, target, action, value):
        label, prefix = DEVICES[target]

        def reply(text, status):
            return {"type": "answer", "text": text, "source": "mqtt", "device_status": status}

        with self.condition:
            self.condition.wait_for(lambda: self.connected and self.subscribed and
                                    (self.availability is not None or bool(self.states)),
                                    timeout=self.timeout)
            if not self.connected or not self.subscribed:
                return reply("Nie mam połączenia z brokerem MQTT lub dostępu do tematów stanu. Nie wysłałam polecenia.", "disconnected")
            if self.availability == "offline":
                return reply("Sterownik LED zgłosił offline. Nie wysłałam polecenia.", "offline")
            if action == "status":
                state = self.states.get(prefix + "/state")
                brightness = self.states.get(prefix + "/brightness/state")
                if state is None:
                    return reply(f"Nie mam zgłoszonego stanu LED-ów {label}.", "unknown")
                level = f", jasność około {round(brightness[0] * 100 / 255)} procent" if brightness else ""
                return reply(f"Ostatni zgłoszony stan LED-ów {label}: {'włączone' if state[0] else 'wyłączone'}{level}.", "reported")
            if action == "adjust_brightness":
                brightness = self.states.get(prefix + "/brightness/state")
                if brightness is None:
                    return reply(f"Nie znam obecnej jasności LED-ów {label}. Podaj ją wprost, np. pięćdziesiąt procent.", "unknown")
                step = raw_brightness(abs(value)) * (1 if value >= 0 else -1)
                action, value = "brightness", max(0, min(255, brightness[0] + step))
            suffix = "" if action == "power" else "/brightness"
            state_topic = prefix + suffix + "/state"
            previous = self.states.get(state_topic)
            reported = ("włączone" if value else "wyłączone") if action == "power" else f"jasność około {round(value * 100 / 255)} procent"
            if previous and previous[0] == value:
                return reply(f"Ostatni zgłoszony stan LED-ów {label} już odpowiada poleceniu: {reported}.", "reported")
            baseline = self.sequence
            try:
                # Bez retained i bez aplikacyjnego ponawiania komend po błędzie.
                info = self.client.publish(prefix + suffix + "/set", str(value), qos=0, retain=False)
            except (ValueError, RuntimeError, OSError):
                logging.exception("Nie udało się wysłać komendy LED")
                return reply("Nie udało się wysłać polecenia MQTT.", "failed")
            if info.rc != 0:
                return reply("Nie udało się przekazać polecenia MQTT.", "failed")

            def confirmed():
                state = self.states.get(state_topic)
                return bool(state and state[0] == value and state[1] > baseline and not state[2])

            self.condition.wait_for(lambda: confirmed() or not self.connected or self.availability == "offline",
                                    timeout=self.timeout)
            if confirmed() and self.connected and self.availability != "offline":
                return reply(f"Sterownik zgłosił dla LED-ów {label}: {reported}.", "confirmed")
            return reply(f"Wysłałam polecenie dla LED-ów {label}, ale nie dostałam nowego potwierdzenia stanu.", "sent_unconfirmed")

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


def load_lights(config):
    if config is None:
        return DisabledLights()
    try:
        return MQTTLights(config)
    except ImportError:
        logging.warning("Brakuje python3-paho-mqtt; sterowanie LED pozostaje wyłączone")
        return DisabledLights("Brakuje modułu MQTT na Pi 5. Zainstaluj pakiet python3-paho-mqtt i uruchom ponownie ewa-brain.")
