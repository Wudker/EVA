# Instalacja i odbudowa E.W.A.

Folder ma nazwę `instalation`, zgodnie z ustaleniem. Skrypty uruchamiaj
zwykłym kontem przez `bash`, bez `sudo` przed nazwą skryptu. O uprawnienia
sudo poproszą tylko operacje systemowe. Repo powinno być w ścieżce bez spacji.

## Co wybrać

| Sytuacja | Polecenie z głównego katalogu EVA |
| --- | --- |
| Działająca Pi3, dodanie głośnika | `bash instalation/install_pi3.sh --tts-only` |
| Pi3, instalacja pakietów, Vosk i TTS | `bash instalation/install_pi3.sh` |
| Pi3 z modelem Vosk w innym miejscu | `bash instalation/install_pi3.sh --skip-model` (potem `--model ŚCIEŻKA` w kliencie) |
| Pi5 z działającym USB | `bash instalation/install_pi5.sh` |
| Pi5, odbudowa wraz z profilem USB | `bash instalation/install_pi5.sh --usb-interface usb0` |
| Sam istniejący interfejs USB Pi5 | `bash instalation/configure_usb.sh --role pi5 --interface usb0` |
| Sam istniejący interfejs USB Pi3 | `bash instalation/configure_usb.sh --role pi3 --interface usb0` |
| Sprawdzenie planu bez zmian | Dodaj `--dry-run` do instalatora lub `configure_usb.sh` |
| Kopia pamięci Pi5 | `bash instalation/backup_pi5.sh` |
| Przywrócenie kopii Pi5 | `bash instalation/restore_pi5.sh /pełna/ścieżka/ewa-state-....tar.gz` |

Instalatory nie restartują całego systemu. `install_pi5.sh` restartuje
Ollamę i usługę `ewa-brain`, więc uruchamiaj go po zakończeniu rozmowy.
Na działającej Pi5 dodanie TTS po stronie Pi3 nie wymaga reinstalowania mózgu.

## Czysta Pi5

1. Zainstaluj Raspberry Pi OS 64-bit, skonfiguruj Wi-Fi, konto i SSH w Imagerze.
2. Połącz się przez Wi-Fi; podłącz działającą Pi3 dotychczasowym przewodem.
3. Pobierz repo i sprawdź nazwę USB:

```bash
sudo apt update
sudo apt install -y git python3
cd ~
git clone https://github.com/Wudker/EVA.git
cd ~/EVA
ip -br link
```

4. Jeśli interfejs nazywa się `usb0`:

```bash
bash instalation/install_pi5.sh --usb-interface usb0 --dry-run
bash instalation/install_pi5.sh --usb-interface usb0
python3 instalation/doctor.py --role pi5 --chat
```

Instalator wymaga architektury `arm64` i Pythona >= 3.10. Tworzy usługę
z rzeczywistą nazwą użytkownika i ścieżką repo, więc nie zakłada `/home/soul`.
Domyślny nowy model to `ewa` na bazie `qwen2.5:3b`. Jeśli `ewa` już istnieje,
nie jest przebudowywany. Istniejący `config/Modelfile` również jest zachowywany.
`--base-model` wpływa wyłącznie na nowy plik Modelfile, gdy brakuje pliku i modelu.
Skrypt nie jest narzędziem do przełączania modelu już działającej E.W.A.

Systemowe pliki usługi/ustawień Ollamy są generowane na nowo; poprzednie
różniące się wersje otrzymują kopię `.bak.DATA` obok oryginału. Niestandardowe
ustawienia systemd wymagają sprawdzenia po instalacji. Dane projektu i historia
nie są kasowane. Domyślne ścieżki pamięci to `Eva-AI/EWA_Brain/memory/`.

Po instalacji przetestuj restart:

```bash
sudo reboot
```

Po ponownym SSH:

```bash
systemctl is-active ollama ewa-brain
curl --max-time 10 http://10.77.77.1:8000/health
```

Na Pi3 sprawdź ten sam `/health` i jedno pytanie przez klienta.

## USB i Pi3 — granice automatyzacji

`configure_usb.sh` tworzy lub aktualizuje jeden profil `ewa-usb` wskazanego
interfejsu. Nie usuwa innych profili ani nie zmienia Wi-Fi. Ustawia statyczne
adresy Pi5 `10.77.77.1/24`, Pi3 `10.77.77.2/24`, bez trasy domyślnej przez USB.
Przy istniejącym profilu innego interfejsu lub zduplikowanej nazwie zatrzymuje się.
Konfigurację wykonuj przez Wi-Fi. Nie zgaduj interfejsu, jeśli `usb0` nie występuje.

Nie automatyzowano przygotowania nowej karty SD, wyboru Wi-Fi/hasła, sterownika
ReSpeakera ani utworzenia urządzenia USB gadget na świeżej Pi3. Te elementy
zależą od rzeczywistego sprzętu, wersji systemu i sterownika. Twój działający
gadget i audio na Pi3 pozostają punktem wyjścia. `install_pi3.sh` instaluje
aplikację i zależności, a nie cały obraz systemu.

## Kopia i przywracanie

Zamknij klienta głosowego i ewentualny `server.py` uruchomiony ręcznie.
Wrappery zarządzają usługą systemd `ewa-brain`; nie zatrzymują dowolnych procesów.

```bash
bash instalation/backup_pi5.sh
```

Kopia w `~/ewa-backups/ewa-state-DATA-LOSOWE.tar.gz` obejmuje, jeśli istnieją:

- `config/Modelfile` i `config/brain_rules.txt`;
- `memory/projects.json` oraz poprzednią wersję `.bak`;
- samodzielną bazę `memory/conversations.db` utworzoną przez API backup SQLite;
- informacyjny eksport `ollama show ewa --modelfile` i manifest z sumami SHA-256.

Obejmuje standardowy katalog pamięci w repo, nie niestandardowe `EVA_DB_PATH`
lub `EVA_PROJECTS_PATH`. Nie jest obrazem SD. Nie zawiera wag modeli, Vosk,
konfiguracji Wi-Fi/ALSA/gadgetu, poświadczeń brokera ani lokalnych ustawień Pi3.
Na Pi3 osobno zachowaj `Eva-hand/audio.local.json`, jeśli go utworzyłeś.
Limit pojedynczego pliku wynosi 64 MiB, a całej kopii 128 MiB; większa historia
wymaga rozszerzenia limitu albo osobnej kopii bazy.

Na odbudowanej Pi5, po instalacji aplikacji:

```bash
bash instalation/restore_pi5.sh /pełna/ścieżka/ewa-state-....tar.gz
```

Skrypt weryfikuje kopię przed zmianami, zatrzymuje działającą usługę, zapisuje
kopię obecnego stanu i przywraca wybrane pliki. Następnie odtwarza poprzedni
stan uruchomienia usługi. Nie przywraca systemowych plików `/etc` ani wag modelu.
Przy przerwaniu przywracania pozostaje kopia stanu sprzed operacji.

Jeśli przywrócony Modelfile zawiera właściwą wcześniejszą personalizację,
odtwórz z niego model, a potem sprawdź API:

```bash
ollama create ewa -f Eva-AI/EWA_Brain/config/Modelfile
sudo systemctl restart ewa-brain
python3 instalation/doctor.py --role pi5 --chat
```

Model bazowy wskazany w `FROM` musi być dostępny; dla zwykłej nazwy można użyć
`ollama pull NAZWA_MODELU`. Plik z `FROM /ścieżka/do/modelu.gguf` wymaga osobno
tego pliku. Informacyjny eksport Ollamy często wskazuje lokalny blob; sam eksport
nie zastępuje jego wag. Kopia nie gwarantuje odtworzenia dokładnego modelu,
jeśli lokalny Modelfile był nieaktualny albo brak źródłowych wag.

## Testy bez sprzętu

Z katalogu repo:

```bash
python3 -m unittest discover -s Eva-hand/tests -v
python3 -m unittest discover -s instalation/tests -v
cd Eva-AI/EWA_Brain
python3 -m unittest discover -s tests -v
```

Testy sprawdzają m.in. błąd głośnika bez ponawiania pytania, zamykanie procesów,
przekazywanie polskiego tekstu, odczyt lokalnej konfiguracji, kopię SQLite z WAL,
przywracanie oraz odrzucanie uszkodzonych archiwów i niebezpiecznych ścieżek.
Próby instalatorów są wykonywane bez zmian systemowych, przez `--dry-run`.
