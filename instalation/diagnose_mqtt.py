#!/usr/bin/env python3
"""Odczytaj połączenie i ostatnie stany MQTT; niczego nie włączaj ani nie wyłączaj."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Eva-AI" / "EWA_Brain"))
from modules.smartroom import MQTTLights, DEVICES, read_config


def main():
    config = read_config(ROOT / "Eva-AI/EWA_Brain/config/mqtt.local.json")
    if config is None:
        print("Najpierw uruchom: python3 instalation/configure_mqtt.py", file=sys.stderr)
        return 1
    controller = MQTTLights(config)
    try:
        with controller.condition:
            controller.condition.wait_for(lambda: controller.connected and controller.subscribed and
                (controller.availability == "offline" or len(controller.states) == 4), timeout=5)
            print("Broker:", f"{config['host']}:{config['port']}")
            print("Połączenie:", "OK" if controller.connected else "BRAK")
            print("Subskrypcje:", "OK" if controller.subscribed else "BRAK")
            print("Dostępność sterownika:", controller.availability or "nieznana (możliwy starszy firmware)")
            for name, prefix in DEVICES.values():
                state = controller.states.get(prefix + "/state")
                brightness = controller.states.get(prefix + "/brightness/state")
                print(f"LED-y {name}: stan={state[0] if state else 'brak'}, jasność={brightness[0] if brightness else 'brak'}/255")
            print("To ostatnie wartości zgłoszone przez MQTT, także zapisane wcześniej przez brokera.")
            return 0 if controller.connected and controller.subscribed else 1
    finally:
        controller.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError:
        print("Zainstaluj na Pi 5: sudo apt install python3-paho-mqtt", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as exc:
        print(f"Błąd MQTT: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
