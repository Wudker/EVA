#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$EWA_INSTALL_DIR/state_service.sh"
(( $# == 1 )) || die 'Użycie: bash instalation/restore_pi5.sh /pełna/ścieżka/ewa-state-....tar.gz'
check_user
check_python
archive="$(realpath -- "$1")"
python3 "$EWA_INSTALL_DIR/state.py" verify "$archive"
echo 'Zamknij wcześniej serwer.py uruchomiony ręcznie. Przed przywróceniem powstanie kopia obecnego stanu.'
pause_brain
python3 "$EWA_INSTALL_DIR/state.py" backup
python3 "$EWA_INSTALL_DIR/state.py" restore "$archive"
echo 'Pamięć i pliki konfiguracji przywrócone. Wagi modelu nie są częścią kopii.'
echo 'Aby odtworzyć model z przywróconego Modelfile: ollama create ewa -f Eva-AI/EWA_Brain/config/Modelfile'
