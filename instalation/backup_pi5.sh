#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$EWA_INSTALL_DIR/state_service.sh"
(( $# == 0 )) || die 'Użycie: bash instalation/backup_pi5.sh'
check_user
check_python
echo 'Zamknij wcześniej serwer.py uruchomiony ręcznie. Usługę systemd skrypt zatrzyma na czas kopii.'
pause_brain
python3 "$EWA_INSTALL_DIR/state.py" backup
echo 'Kopia gotowa w ~/ewa-backups. Skopiuj ją na komputer; nie dodawaj do GitHuba.'
