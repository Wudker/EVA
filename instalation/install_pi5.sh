#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usb_interface=""; base_model=qwen2.5:3b
while (( $# )); do
    case "$1" in
        --usb-interface) [[ $# -ge 2 ]] || die 'Brakuje nazwy interfejsu'; usb_interface="$2"; shift 2 ;;
        --base-model) [[ $# -ge 2 ]] || die 'Brakuje modelu bazowego'; base_model="$2"; shift 2 ;;
        --dry-run) EWA_DRY_RUN=1; shift ;;
        --help) echo 'bash instalation/install_pi5.sh [--usb-interface usb0] [--base-model qwen2.5:3b] [--dry-run]'; exit 0 ;;
        *) die "Nieznany argument: $1" ;;
    esac
done
check_user
[[ "$base_model" =~ ^[a-zA-Z0-9_./:-]+$ ]] || die 'Niepoprawna nazwa modelu.'
brain="$EWA_REPO_DIR/Eva-AI/EWA_Brain"
[[ -f "$brain/server.py" ]] || die 'Brakuje kodu EWA_Brain. Rozpakuj paczkę do głównego katalogu repo.'
if (( ! EWA_DRY_RUN )); then
    [[ "$(dpkg --print-architecture)" == arm64 ]] || die 'Pi 5 wymaga 64-bitowego systemu arm64.'
fi
run sudo apt-get update
run sudo apt-get install -y python3 curl ca-certificates zstd git
check_python
if [[ -n "$usb_interface" ]]; then
    extra=(); (( ! EWA_DRY_RUN )) || extra+=(--dry-run)
    bash "$EWA_INSTALL_DIR/configure_usb.sh" --role pi5 --interface "$usb_interface" "${extra[@]}"
fi
export OLLAMA_HOST=127.0.0.1:11434
if ! command -v ollama >/dev/null; then
    if (( EWA_DRY_RUN )); then
        echo '[plan] Pobierz oficjalny instalator https://ollama.com/install.sh i uruchom go jako zwykły użytkownik.'
    else
        temporary="$(mktemp)"
        trap 'rm -f -- "$temporary"' EXIT
        curl --fail --location --retry 2 --connect-timeout 15 --max-time 120 https://ollama.com/install.sh -o "$temporary"
        sh "$temporary"
        rm -f -- "$temporary"
        trap - EXIT
    fi
fi
write_root_file /etc/systemd/system/ollama.service.d/ewa.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF
run sudo systemctl daemon-reload
run sudo systemctl enable ollama
run sudo systemctl restart ollama
if (( ! EWA_DRY_RUN )); then
    ready=0
    for attempt in {1..30}; do
        if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then ready=1; break; fi
        sleep 1
    done
    (( ready )) || die 'Ollama nie uruchomiła się. Sprawdź journalctl -u ollama.'
fi
model_exists=0
if (( ! EWA_DRY_RUN )) && ollama show ewa >/dev/null 2>&1; then model_exists=1; fi
if [[ ! -e "$brain/config/Modelfile" ]] && (( ! model_exists )); then
    if (( EWA_DRY_RUN )); then
        echo "[plan] Utwórz brakujący config/Modelfile z bazą $base_model."
    else
        sed "s|@BASE_MODEL@|$base_model|" "$EWA_INSTALL_DIR/Modelfile.example" > "$brain/config/Modelfile"
    fi
elif [[ -e "$brain/config/Modelfile" ]]; then
    echo 'Zachowuję istniejący config/Modelfile.'
else
    echo 'Model ewa istnieje bez lokalnego Modelfile. Nie tworzę zastępczej personalizacji.'
fi
if (( EWA_DRY_RUN )); then
    echo '[plan] Jeśli model ewa istnieje: zachowaj go. Jeśli brakuje: ollama create ewa z lokalnego Modelfile.'
elif (( model_exists )); then
    echo 'Model ewa już istnieje; nie przebudowuję go.'
else
    source_model="$(awk 'toupper($1) == "FROM" {print $2; exit}' "$brain/config/Modelfile")"
    if [[ "$source_model" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_./:-]*$ && "$source_model" != *.gguf ]]; then
        if ! ollama show "$source_model" >/dev/null 2>&1; then ollama pull "$source_model"; fi
    fi
    ollama create ewa -f "$brain/config/Modelfile"
fi

ewa_user="$(id -un)"
write_root_file /etc/systemd/system/ewa-brain.service <<EOF
[Unit]
Description=E.W.A. Brain (USB)
After=network-online.target ollama.service
Wants=network-online.target
Requires=ollama.service

[Service]
Type=simple
User=$ewa_user
WorkingDirectory=$brain
Environment=EVA_MODEL=ewa
Environment=EVA_USB_IP=10.77.77.1
ExecStart=/usr/bin/python3 $brain/server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
run sudo systemctl daemon-reload
run sudo systemctl enable ewa-brain
run sudo systemctl restart ewa-brain
if (( ! EWA_DRY_RUN )); then
    ready=0
    for attempt in {1..15}; do
        if curl --noproxy '*' -fsS --max-time 5 http://10.77.77.1:8000/health; then ready=1; break; fi
        sleep 1
    done
    echo
    (( ready )) || die 'Instalacja zakończona, ale /health nie jest gotowe. Uruchom doctor.py --role pi5; sprawdź USB.'
fi
echo 'Koniec. Sprawdź restart Pi5 oraz /health z Pi3. Skrypt nie wykonuje reboot.'
