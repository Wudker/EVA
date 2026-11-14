#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
tts_only=0; skip_model=0
while (( $# )); do
    case "$1" in
        --tts-only) tts_only=1; shift ;;
        --skip-model) skip_model=1; shift ;;
        --dry-run) EWA_DRY_RUN=1; shift ;;
        --help) echo 'bash instalation/install_pi3.sh [--tts-only | --skip-model] [--dry-run]'; exit 0 ;;
        *) die "Nieznany argument: $1" ;;
    esac
done
check_user
run sudo apt-get update
if (( tts_only )); then
    run sudo apt-get install -y espeak-ng alsa-utils
else
    run sudo apt-get install -y python3 python3-venv python3-dev build-essential libffi-dev pkg-config curl unzip ca-certificates alsa-utils espeak-ng
    extra=(); (( ! skip_model )) || extra+=(--skip-model)
    run bash "$EWA_REPO_DIR/Eva-hand/setup_pi3.sh" "${extra[@]}"
fi
check_python
echo 'Gotowe. Test: python3 Eva-hand/voice_client.py --tts-test'
echo 'Sterownik ReSpeakera, mikser ALSA i konfiguracja gadgetu USB wymagają osobnego sprawdzenia.'
