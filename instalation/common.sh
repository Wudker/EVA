#!/usr/bin/env bash
# Wspólne funkcje. Skrypty uruchamiaj jako zwykły użytkownik; sudo tylko tam, gdzie potrzebne.
set -euo pipefail
EWA_INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EWA_REPO_DIR="$(cd -- "$EWA_INSTALL_DIR/.." && pwd)"
EWA_DRY_RUN=0

die() { echo "Błąd: $*" >&2; exit 1; }
run() {
    if (( EWA_DRY_RUN )); then
        printf '[plan]'; printf ' %q' "$@"; printf '\n'
    else
        "$@"
    fi
}
check_user() {
    if (( ! EWA_DRY_RUN )); then
        [[ $EUID -ne 0 ]] || die 'Uruchom przez bash, bez sudo przed nazwą skryptu.'
        command -v sudo >/dev/null || die 'Brakuje sudo.'
        command -v apt-get >/dev/null || die 'Ten instalator wymaga systemu Debian/Raspberry Pi OS.'
    fi
    # Bez spacji i znaków specjalnych: ścieżki trafią do jednostki systemd.
    [[ "$EWA_REPO_DIR" =~ ^/[a-zA-Z0-9_./-]+$ ]] || die 'Przenieś repo do ścieżki bez spacji, np. ~/EVA.'
}
check_python() {
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Wymagany Python >= 3.10")'
}
write_root_file() {
    local destination="$1" temporary result=0
    if (( EWA_DRY_RUN )); then
        echo "[plan] Plik systemowy: $destination (poprzednia wersja otrzyma kopię .bak)"
        cat
        return
    fi
    temporary="$(mktemp)"
    cat > "$temporary"
    if ! sudo cmp -s "$temporary" "$destination"; then
        if sudo test -e "$destination"; then
            sudo cp -a -- "$destination" "$destination.bak.$(date +%Y%m%d-%H%M%S)" || result=$?
        fi
        if (( result == 0 )); then
            sudo install -D -m 0644 "$temporary" "$destination" || result=$?
        fi
    fi
    rm -f -- "$temporary"
    return "$result"
}
