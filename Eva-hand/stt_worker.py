#!/usr/bin/env python3
"""Jedno rozpoznanie w oddzielnym procesie; stdout zawiera tylko JSON."""
import argparse
import contextlib
import json
import sys
from pathlib import Path

from voice_client import ClientError, load_vosk, transcribe_wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            model, factory = load_vosk(args.model)
            text = transcribe_wav(args.wav, model, factory)
        print(json.dumps({"text": text}, ensure_ascii=False), flush=True)
        return 0
    except ClientError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
