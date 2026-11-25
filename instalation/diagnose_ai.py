#!/usr/bin/env python3
"""Porównaj treść odpowiedzi: prosty tekst, prosty JSON, pełne instrukcje Ewy."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from benchmark_ai import QUICK_CASES, b, model_info, print_metrics

SIMPLE_RULES = ("Jesteś E.W.A., osobistą asystentką Bartłomieja. "
                "Odpowiadaj po polsku, krótko i konkretnie. "
                "Jeśli czegoś nie wiesz, powiedz o tym. "
                "Nie masz narzędzia do sterowania światłem ani urządzeniami. "
                "Nie twierdź ani nie obiecuj, że wykonasz takie działanie.")
SIMPLE_SCHEMA = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["answer", "clarify"]},
    "text": {"type": "string"}}, "required": ["type", "text"], "additionalProperties": False}


def payload_for(mode, question, context):
    payload = b.build_payload([], question, context, compact=True)
    if mode != "ewa":
        system = SIMPLE_RULES
        if mode == "short-json":
            system += ' Zwróć JSON z polami type (answer albo clarify) oraz text (odpowiedź).'
            payload["format"] = SIMPLE_SCHEMA
        else:
            del payload["format"]
        payload["messages"][0]["content"] = system
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("all", "plain", "short-json", "ewa"), default="all")
    parser.add_argument("--model", help="inny już zainstalowany model; nie zmienia usługi Ewy")
    args = parser.parse_args(argv)
    if args.model:
        b.MODEL = args.model
    model_info()
    print("Nie zmieniam modelu ewa, usługi, konfiguracji ani pamięci rozmów.", flush=True)
    print("Test jakości, nie ranking szybkości: różne instrukcje zmieniają pamięć podręczną promptu.", flush=True)
    modes = ("plain", "short-json", "ewa") if args.mode == "all" else (args.mode,)
    errors = 0
    with tempfile.TemporaryDirectory(prefix="ewa-diagnose-") as folder:
        projects = Path(folder) / "projects.json"
        source = b.PROJECTS_PATH if b.PROJECTS_PATH.exists() else b.PROJECTS_PATH.with_name("projects.example.json")
        shutil.copy2(source, projects)
        memory = b.MemoryStore(projects, Path(folder) / "test.db")
        for mode in modes:
            print(f"\nTRYB: {mode}", flush=True)
            for question, expected in QUICK_CASES:
                print(f"\nPytanie: {question}", flush=True)
                payload = payload_for(mode, question, memory.get_context(question, []))
                start = time.monotonic()
                try:
                    raw = b.make_request_json(b.OLLAMA_URL + "/api/chat", payload, 360)
                    print_metrics({"_ollama_timings": b.ollama_timings(raw)}, time.monotonic() - start)
                    answer = raw["message"]["content"]
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("brak treści odpowiedzi")
                    print("Surowa odpowiedź:", answer, flush=True)
                    if raw.get("done_reason") == "length" or raw.get("done") is False:
                        raise ValueError("odpowiedź nieukończona lub ucięta limitem tokenów")
                    if mode != "plain":
                        parsed = json.loads(answer)
                        if (not isinstance(parsed, dict) or parsed.get("type") not in ("answer", "clarify") or
                                not isinstance(parsed.get("text"), str) or not parsed["text"].strip()):
                            raise ValueError("brak poprawnych pól type i text")
                except (b.OllamaError, KeyError, TypeError, ValueError) as exc:
                    errors += 1
                    print(f"BŁĄD techniczny: {exc}", flush=True)
                print("Oczekiwane:", expected, flush=True)
    print("\nPorównaj TREŚĆ z oczekiwanym wynikiem. Kod wyjścia oznacza tylko błędy techniczne.")
    print("plain poprawny, short-json błędny: podejrzenie formatu/instrukcji JSON.")
    print("short-json poprawny, ewa błędny: podejrzenie rozbudowanych instrukcji/kontekstu Ewy.")
    print("Błędy także w plain: sam pełny prompt Ewy nie wystarcza do wyjaśnienia problemu.")
    return int(errors > 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrzerwano test.")
        raise SystemExit(130)
    except (OSError, ValueError) as exc:
        print(f"Błąd danych testowych: {exc}", file=sys.stderr)
        raise SystemExit(1)
