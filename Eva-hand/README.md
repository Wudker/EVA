# Eva-hand — mikrofon i głośnik na Pi 3, AI na Pi 5

Zacznij od [instrukcji etapu 2](../START_ETAP2_GLOS_GLOSNIK.md).

Nowe: `--speak` odczytuje pole `text` odpowiedzi przez eSpeak NG i ALSA.
`--tts-test` sprawdza sam głośnik, a `--list-outputs` pokazuje wyjścia audio.
Nagranie, rozpoznawanie, zapytanie AI i odtwarzanie są wykonywane kolejno.
Bez `--speak` lub konfiguracji `speak: true` pozostaje dotychczasowy tryb tekstowy.
Ustawienia można zapisać w ignorowanym przez Git pliku `audio.local.json`,
utworzonym na podstawie `audio.example.json`. Parametry CLI mają pierwszeństwo.
`--no-speak` wyłącza głośnik także przy włączonym odczycie w konfiguracji.

Domyślnie Enter rozpoczyna 8 sekund nagrania mono PCM 16 bit / 16 kHz.
Vosk rozpoznaje tekst, a program wysyła go do `/chat` istniejącego
`EWA_Brain`. ReSpeaker jest podłączony do Pi 3, nie do komputera z SSH.
Model jest ładowany raz; na Pi 5 wysyłany jest tekst, nie plik audio.
Nie ma jeszcze wykrywania ciszy ani słowa wybudzającego.

## Najważniejsze polecenia (w katalogu Eva-hand)

```bash
# Czy serwer i model ewa są widoczne?
python3 voice_client.py --check

# Jedno pytanie bez mikrofonu i Vosk
python3 voice_client.py --text "Ile to dwa plus dwa?"

# Karty i urządzenia ALSA
python3 voice_client.py --list-devices

# Sprawdzenie rozpoznawania bez wysyłania tekstu do AI
.venv/bin/python voice_client.py --stt-only --once

# Rozmowa z nagraniami po 12 sekund
.venv/bin/python voice_client.py --seconds 12

# Własna sesja = oddzielna historia po stronie Pi 5
.venv/bin/python voice_client.py --session test-glosu-2

# Pełna lista parametrów
python3 voice_client.py --help

# Testy bez Vosk, mikrofonu i Ollamy
python3 -m unittest discover -s tests -v
```

`--device` przyjmuje nazwę ALSA. Użyj nazwy ReSpeakera z listy urządzeń,
np. `--device plughw:CARD=NAZWA_KARTY,DEV=0`, podstawiając rzeczywistą nazwę.
Domyślnie jest `default`; program nie zgaduje numeru karty.
Jeśli sterownik zgłasza brak obsługi mono, spróbuj `plughw` zamiast `hw`.

`--model` wskazuje rozpakowany model Vosk. Domyślnie jest to
`models/vosk-model-small-pl-0.22` obok skryptu; ścieżka nie zależy od
bieżącego katalogu terminala.

`--wav /ścieżka/test.wav` pozwala sprawdzić istniejące nagranie bez mikrofonu.
WAV musi mieć 1 kanał, 16 bit PCM, 16000 Hz. Z `--stt-only` nic nie trafia do API.
Przy `--wav` plik wejściowy użytkownika nie jest usuwany.

`--server` to adres bez końcówki `/chat`, domyślnie `http://10.77.77.1:8000`.
`--session` to 1–32 znaków ASCII: litery, cyfry, podkreślenie lub myślnik.
Domyślna sesja `ewa-voice` jest zachowywana także po restarcie klienta,
bo historia pozostaje w istniejącej bazie na Pi 5.

`--seconds` przyjmuje 1–30 sekund. Wypowiedzi powyżej 600 znaków są odrzucane
przed wysłaniem, zgodnie z limitem serwera. Pusty wynik STT niczego nie wysyła.
Klient nie ponawia sam żądania `/chat` po błędzie; po timeout serwer może
jeszcze kończyć poprzednie zapytanie. Kolejne pytanie zacznij po sprawdzeniu stanu.

## Jeśli coś nie działa

| Objaw | Co sprawdzić |
| --- | --- |
| Nie działa `.local` w SSH | Aktualny adres Wi-Fi z routera albo przeskok przez Pi 5 |
| `Connection refused` do API | Czy `server.py`/`ewa-brain` działa na Pi 5 |
| Timeout połączenia do API | `ping -c 3 10.77.77.1` z Pi 3, `ip -4 addr show usb0` na obu Pi |
| HTTP 503 lub `model_missing` | `ollama list` i `systemctl status ollama --no-pager` na Pi 5 |
| HTTP 502 | Logi `ewa-brain` lub terminal, w którym uruchomiono serwer |
| Brakuje `vosk` | `bash setup_pi3.sh`, uruchamianie przez `.venv/bin/python` |
| `No matching distribution` | Zapisz wynik `uname -m`, `python3 --version` i `.venv/bin/python -m pip --version` |
| `Unknown PCM` albo błąd urządzenia | `--list-devices`, właściwy `--device`; sterownik ReSpeakera musi być już zainstalowany |
| `Device or resource busy` | Zakończ wcześniejszy program nasłuchujący na tym samym mikrofonie |
| Pusty tekst mimo mówienia | `--stt-only`, dobór karty, ustawienia wejścia w `alsamixer` |
| Vosk myli nazwę SmartRoom | Porównaj wydruk `Ty:` z wypowiedzią; to problem STT, nie połączenia USB |

Instalator nie konfiguruje sterownika ReSpeakera ani sieci USB.
Korzysta z dotychczasowej konfiguracji tych elementów.

Jeżeli instalacja zależności wymaga kompilacji CFFI (np. brak gotowego pakietu
dla używanego 32-bitowego systemu) i zgłosi brak kompilatora, `ffi.h` lub
`Python.h`, doinstaluj narzędzia i ponów instalację:

```bash
sudo apt install -y build-essential python3-dev libffi-dev pkg-config
bash setup_pi3.sh
```

