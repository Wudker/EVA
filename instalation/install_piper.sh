#!/usr/bin/env bash
# Opcjonalna odbudowa TTS. Gdy próbka Gosi już działa, pomiń ten instalator.
set -euo pipefail
if [[ ${1:-} == --help ]]; then
    echo "Użycie: bash instalation/install_piper.sh [katalog, domyślnie ~/ewa-tts]"
    echo "Piper 1.8.0 dla ARM64; wymaga python3 z modułem venv i dostępu do internetu."
    exit 0
fi
if [[ $# -gt 1 ]]; then echo "Zbyt wiele argumentów" >&2; exit 2; fi
if [[ $(dpkg --print-architecture) != arm64 ]]; then
    echo "Ten instalator obsługuje system ARM64. Nie zmieniam konfiguracji." >&2
    exit 1
fi
tts_dir=${1:-"$HOME/ewa-tts"}
mkdir -p -- "$tts_dir"
cd -- "$tts_dir"
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --only-binary=:all: 'piper-tts==1.8.0'
if [[ ! -s pl_PL-gosia-medium.onnx || ! -s pl_PL-gosia-medium.onnx.json ]]; then
    .venv/bin/python -m piper.download_voices pl_PL-gosia-medium
fi
echo "Piper gotowy. Dalsze polecenia uruchamiaj z katalogu repozytorium:"
echo "Lokalny głos na Pi 3: python3 instalation/configure_piper.py"
echo "Serwer głosu na Pi 5: patrz START_ETAP6_AUDIO.md (bez configure_piper.py)."
