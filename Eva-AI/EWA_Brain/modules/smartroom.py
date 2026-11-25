"""Dwa obwody LED SmartRoom: jawne polecenia i stan pochodzący z MQTT."""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid

from .calculator import integer
from .memory_manager import normal

AVAILABILITY = "smartroom/workshop/led_controller/availability"
DEVICES = {"bed": ("przy łóżku", "smartroom/workshop/led_bed"),
           "shelf": ("na półce", "smartroom/workshop/led_shelf")}
STATE_TOPICS = [AVAILABILITY, *[prefix + suffix for _, prefix in DEVICES.values()
                               for suffix in ("/state", "/brightness/state")]]
TARGETS = {"bed": {"lozko", "lozka", "przy lozku", "lozku"},
           "shelf": {"polka", "polke", "polki", "na polce", "polce"}}
GENERIC = {"", "ledy", "led", "ledow", "swiatlo", "oswietlenie", "swiatla",
           "ledy w pokoju", "swiatlo w pokoju", "oswietlenie w pokoju"}


def normalize(text):
    return normal(text).replace("ł", "l").strip(" .!?")


def target_from(text):
    text = normalize(text)
    text = re.sub(r"^(?:ledy|led|ledow|swiatlo|swiatla|oswietlenie) ", "", text)
    return next((key for key, variants in TARGETS.items() if text in variants), None)


def parse_command(text):
    """Rozpoznaj całe polecenie. Pytania, negacje i instrukcje złożone nie uruchamiają sprzętu."""
    text = normalize(text)
    match = re.fullmatch(r"(wlacz|wylacz|zapal|zgas) (.+)", text)
    if match:
        verb, target = match.groups()
        action, value = "power", int(verb in ("wlacz", "zapal"))
    else:
        match = re.fullmatch(r"ustaw jasnosc(?: (.+?))? na (.+?)\s*(?:procent|%)", text)
        if match:
            target, spoken = match.groups()
            try:
                percent = integer(spoken.split())
                if not 0 <= percent <= 100:
                    raise ValueError()
            except ValueError:
                return {"error": "Podaj jasność od zera do stu procent."}
            action, value = "brightness", (percent * 255 + 50) // 100
            target = target or ""
        else:
            match = re.fullmatch(r"(?:podaj stan|sprawdz stan|jaki jest stan|stan) (.+)", text)
            if not match:
                return None
            target, action, value = match.group(1), "status", None
    device = target_from(target)
    if device is None and target not in GENERIC:
        # Nie przechwytuj np. 'włącz muzykę', ale nie zgaduj nieznanego obwodu oświetlenia.
        if action == "brightness" or re.search(r"\b(?:ledy|led|ledow|swiatlo|oswietlenie)\b", target):
            return {"error": "Obsługuję LED-y przy łóżku i na półce. Powiedz, który obwód wybrać."}
        return None
    return {"action": action, "target": device, "value": value}


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
        if (target not in DEVICES or action not in ("power", "brightness", "status") or
                (action != "status" and (type(value) is not int or not 0 <= value <= (1 if action == "power" else 255)))):
            raise ValueError("Polecenie spoza listy dozwolonych działań LED")
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
            suffix = "" if action == "power" else "/brightness"
            state_topic = prefix + suffix + "/state"
            previous = self.states.get(state_topic)
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

            # Firmware publikuje tylko zmiany; identyczne ustawienie nie dostanie nowego stanu.
            if not (previous and previous[0] == value):
                self.condition.wait_for(lambda: confirmed() or not self.connected or self.availability == "offline",
                                        timeout=self.timeout)
            if confirmed() and self.connected and self.availability != "offline":
                reported = ("włączone" if value else "wyłączone") if action == "power" else f"jasność około {round(value * 100 / 255)} procent"
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
