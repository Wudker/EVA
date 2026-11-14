#!/usr/bin/env bash
# Konfiguruje istniejący interfejs USB. Nie instaluje gadgetu ani sterownika ReSpeakera.
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
role=""; interface=""
while (( $# )); do
    case "$1" in
        --role) [[ $# -ge 2 ]] || die 'Brakuje wartości --role'; role="$2"; shift 2 ;;
        --interface) [[ $# -ge 2 ]] || die 'Brakuje wartości --interface'; interface="$2"; shift 2 ;;
        --dry-run) EWA_DRY_RUN=1; shift ;;
        --help) echo 'bash instalation/configure_usb.sh --role pi5|pi3 --interface usb0 [--dry-run]'; exit 0 ;;
        *) die "Nieznany argument: $1" ;;
    esac
done
case "$role" in pi5) address=10.77.77.1/24 ;; pi3) address=10.77.77.2/24 ;; *) die 'Wybierz --role pi5 lub pi3.' ;; esac
[[ "$interface" =~ ^[a-zA-Z0-9_.-]+$ ]] || die 'Podaj --interface, np. usb0.'
case "$interface" in lo|wlan*|wl*|eth0) die 'To nie jest oczekiwany interfejs USB.' ;; esac
check_user
if (( ! EWA_DRY_RUN )); then
    command -v nmcli >/dev/null || die 'Brakuje NetworkManager/nmcli.'
    [[ -d "/sys/class/net/$interface" ]] || die "Nie znaleziono $interface. Sprawdź przewód i ip -br link."
    sys_device="$(readlink -f "/sys/class/net/$interface/device" || true)"
    case "$interface:$sys_device" in usb[0-9]*:*|*:/sys/*/usb*|*:/sys/*gadget*) ;; *) die 'Nie potwierdzono interfejsu USB; sprawdź jego nazwę i sterownik.' ;; esac
fi

write_root_file /etc/NetworkManager/conf.d/90-ewa-usb.conf <<EOF
[device-ewa-usb]
match-device=interface-name:$interface
managed=1
ignore-carrier=1
EOF
run sudo nmcli general reload
settings=(ipv4.method manual ipv4.addresses "$address" ipv4.gateway '' ipv4.dns ''
          ipv4.never-default yes ipv6.method disabled connection.autoconnect yes
          connection.autoconnect-priority 100 connection.autoconnect-retries 0)
if (( EWA_DRY_RUN )); then
    echo "[plan] Utwórz lub zaktualizuj jeden profil ewa-usb dla $interface, adres $address."
    echo '[plan] Pozostałe profile i Wi-Fi pozostają zachowane.'
else
    # Identyfikacja po UUID zapobiega tworzeniu kolejnych profili przy ponownej instalacji.
    profiles="$(nmcli -t -f UUID,NAME connection show)"
    mapfile -t ids < <(printf '%s\n' "$profiles" | awk -F: '$2 == "ewa-usb" {print $1}')
    (( ${#ids[@]} <= 1 )) || die 'Istnieje kilka profili ewa-usb. Uporządkuj je przed konfiguracją.'
    if (( ${#ids[@]} == 1 )); then
        old_interface="$(nmcli -g connection.interface-name connection show uuid "${ids[0]}")"
        old_type="$(nmcli -g connection.type connection show uuid "${ids[0]}")"
        [[ "$old_interface" == "$interface" && "$old_type" == 802-3-ethernet ]] || die 'Istniejący ewa-usb dotyczy innego interfejsu/typu. Nie zmieniono profilu.'
        sudo nmcli connection modify uuid "${ids[0]}" "${settings[@]}"
        sudo nmcli connection up uuid "${ids[0]}"
    else
        sudo nmcli connection add type ethernet ifname "$interface" con-name ewa-usb "${settings[@]}"
        sudo nmcli connection up ewa-usb
    fi
    ip -4 addr show "$interface"
fi
