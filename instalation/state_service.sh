#!/usr/bin/env bash
# Wczytywany po common.sh przez backup/restore. Przywraca poprzedni stan usługi.
pause_brain() {
    sudo -v
    EWA_WAS_ACTIVE=0
    if systemctl is-active --quiet ewa-brain; then
        EWA_WAS_ACTIVE=1
    fi
    trap resume_brain EXIT
    if (( EWA_WAS_ACTIVE )); then sudo systemctl stop ewa-brain; fi
}
resume_brain() {
    local result=$?
    trap - EXIT
    if (( EWA_WAS_ACTIVE )); then
        if ! sudo systemctl start ewa-brain; then
            echo 'Nie udało się ponownie uruchomić ewa-brain. Sprawdź systemctl status ewa-brain.' >&2
            result=1
        fi
    fi
    exit "$result"
}
