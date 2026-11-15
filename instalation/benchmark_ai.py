#!/usr/bin/env python3
"""Pomiar i próbki jakości AI na Pi5; bez zmian historii i pamięci użytkownika."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Eva-AI" / "EWA_Brain"))
from modules import brain as b

QUICK_CASES = [
    ("Ile to trzy plus pięć?", "8, czyli osiem."),
    ("Ile to siedem razy osiem?", "56, czyli pięćdziesiąt sześć."),
    ("Włącz światło w pokoju.", "Informacja o braku narzędzia. Nie wolno twierdzić, że światło włączono."),
]


def print_metrics(answer, wall):
    metrics = answer.get("_ollama_timings", {})
    print(f"Czas wywołania: {wall:.2f} s")
    for key, label in (("load_s", "Ładowanie"), ("prompt_s", "Instrukcje/pytanie"),
                       ("generation_s", "Generowanie"), ("total_s", "Razem w Ollamie")):
        value = metrics.get(key)
        print(f"{label}: {value:.2f} s" if value is not None else f"{label}: brak danych")
    print(f"Tokeny całego JSON: {metrics.get('output_tokens', 'brak')}; "
          f"tokeny/s: {metrics.get('tokens_per_second', 'brak')}")


def model_info():
    print(f"Model testu: {b.MODEL}; Ollama: {b.OLLAMA_URL}", flush=True)
    try:
        info = b.make_request_json(b.OLLAMA_URL + "/api/show", {"model": b.MODEL}, 30)
        details = info.get("details", {})
        print("Rodzina:", details.get("family", "brak"),
              "| rozmiar:", details.get("parameter_size", "brak"),
              "| kwantyzacja:", details.get("quantization_level", "brak"), flush=True)
    except b.OllamaError as exc:
        print(f"Nie odczytano metadanych modelu: {exc}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="opcjonalnie inny JUŻ zainstalowany model; nie zmienia usługi")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--compare", action="store_true", help="te same pytania w starym i krótkim formacie")
    modes.add_argument("--legacy", action="store_true", help="tylko dawny format 9 wymaganych pól")
    modes.add_argument("--warmup", action="store_true", help="jedno pytanie rozgrzewające model i instrukcje")
    parser.add_argument("--full", action="store_true", help="dodatkowo język, niejasne pytanie i propozycja pamięci")
    args = parser.parse_args(argv)
    if args.model:
        b.MODEL = args.model
    model_info()
    print("Nie wymuszam rozładowania modelu. Pierwszy test może mieć zimny lub ciepły start.", flush=True)
    print("To test bez historii rozmowy i bez audio. Oceniaj treść względem oczekiwanego wyniku.", flush=True)
    errors = 0
    try:
        with tempfile.TemporaryDirectory(prefix="ewa-benchmark-") as folder:
            projects = Path(folder) / "projects.json"
            shutil.copy2(b.PROJECTS_PATH, projects)
            memory = b.MemoryStore(projects, Path(folder) / "test.db")
            cases = list(QUICK_CASES)
            if args.warmup:
                cases = [("Cześć.", "Krótkie powitanie; to tylko rozgrzanie modelu.")]
            elif args.full:
                cases += [
                    ("Ile wynosi pierwiastek kwadratowy z dziewięciu?", "3; naturalne zdanie po polsku."),
                    ("Ile to trzy razy trzy razy trzy?", "27."),
                    ("Oblicz trzy plus, nie, chyba razy...", "Prośba o doprecyzowanie działania."),
                ]
                projects_list = memory.load()["projects"]
                if projects_list:
                    project = projects_list[0]
                    cases.append((f"Do projektu {project['name']} dodałem czujnik temperatury.",
                                  "Wydzielenie nowego faktu przez add_fact lub rozpoznanie, że już jest znany. "
                                  "W tym teście nic nie zostanie zapisane."))
            formats = [False, True] if args.compare else [False if args.legacy else True]
            for compact in formats:
                label = "compact (2 wymagane pola)" if compact else "legacy (9 wymaganych pól)"
                print(f"\nFORMAT: {label}", flush=True)
                for number, (question, expected) in enumerate(cases, 1):
                    print(f"\nTEST {number}: {question}", flush=True)
                    context = memory.get_context(question, [])
                    start = time.monotonic()
                    try:
                        answer = b.call_ollama([], question, context, compact=compact)
                    except b.OllamaError as exc:
                        errors += 1
                        print(f"BŁĄD: {exc}", flush=True)
                        continue
                    print_metrics(answer, time.monotonic() - start)
                    print("E.W.A. (odpowiedź testowa):", answer["text"])
                    print("Oczekiwane:", expected)
                    print("Typ:", answer["type"], "| propozycja pamięci:", answer["memory_action"], flush=True)
    except (OSError, ValueError) as exc:
        print(f"Błąd danych testowych: {exc}", file=sys.stderr)
        return 1
    print("\nKoniec. Kod wyjścia sprawdza błędy techniczne, nie poprawność odpowiedzi AI.", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrzerwano test.")
        raise SystemExit(130)
