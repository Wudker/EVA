#!/usr/bin/env bash
# Uruchom na Pi 3: bash setup_pi3.sh
set -euo pipefail
script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_directory"

for command in python3 arecord curl unzip; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Brakuje: $command. Najpierw: sudo apt install python3-venv alsa-utils curl unzip" >&2
        exit 1
    fi
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Wymagany Python 3.10 lub nowszy.")'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

model_name="vosk-model-small-pl-0.22"
model_directory="$script_directory/models/$model_name"
if [[ "${1:-}" == "--skip-model" ]]; then
    echo "Pominięto pobieranie modelu. Podaj jego istniejącą ścieżkę przez --model."
elif [[ -f "$model_directory/am/final.mdl" && -f "$model_directory/conf/mfcc.conf" ]]; then
    echo "Model jest już rozpakowany: $model_directory"
elif [[ -e "$model_directory" ]]; then
    echo "Katalog $model_directory istnieje, ale model jest niekompletny. Sprawdź go przed ponowieniem." >&2
    exit 1
else
    mkdir -p models
    download_directory="$(mktemp -d "$script_directory/models/.download-XXXXXX")"
    trap 'rm -rf -- "$download_directory"' EXIT
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
        "https://alphacephei.com/vosk/models/$model_name.zip" \
        --output "$download_directory/model.zip"
    unzip -q "$download_directory/model.zip" -d "$download_directory"
    test -f "$download_directory/$model_name/am/final.mdl"
    test -f "$download_directory/$model_name/conf/mfcc.conf"
    mv -- "$download_directory/$model_name" "$model_directory"
fi
.venv/bin/python -c 'import vosk; print("Import Vosk: OK")'
echo "Gotowe. Sprawdź mikrofon: .venv/bin/python voice_client.py --list-devices"
echo "Uruchom rozmowę: .venv/bin/python voice_client.py"
