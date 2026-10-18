# E.W.A. Brain v1.0 — pamięć hybrydowa na Raspberry Pi 5

**Cel:** lokalny Qwen (`ollama run ewa` jest już skonfigurowany), osobny JSON na projekty/aliasy oraz osobna baza SQLite na rozmowy, oczekujące potwierdzenia i audyt zmian. Raspberry Pi 3 będzie później wysyłało tekst przez USB/HTTP. **Nie ma jeszcze STT/TTS ani sterowania urządzeniami.**

## 1. Instalacja na Pi 5

Nie trzeba ponownie instalować Ollamy ani tworzyć modelu. Sprawdź:

```bash
ollama list
ip -4 addr show usb0
python3 --version
```

Zakładamy: użytkownik `soul`, Pi 5 ma `10.77.77.1` na `usb0`, istnieje model `ewa` (ten utworzony z Twojego Modelfile), Python >= 3.10.

**Na komputerze z Windows (PowerShell)**, po pobraniu `ewa-brain-memory-v1.zip`:

```powershell
scp .\ewa-brain-memory-v1.zip soul@ADRES_WIFI_PI5:~/
```

**Na Pi 5:**

```bash
sudo apt update
sudo apt install -y python3 unzip
unzip -o ~/ewa-brain-memory-v1.zip -d ~/
cd ~/EWA_Brain
python3 -m unittest discover -s tests -v
python3 server.py
```

Archiwum tworzy `~/EWA_Brain` i **nie zawiera pliku `config/Modelfile`**. Twoje dotychczasowe instrukcje i model `ewa` zostają. Archiwum zawiera jedynie `memory/projects.example.json`. Plik `memory/projects.json` jest tworzony z szablonu **tylko przy pierwszym uruchomieniu**, więc następne wgrywanie kodu nie powinno go nadpisywać. Zawsze rób kopie danych przed aktualizacją.

Brak instalacji pakietów `pip`: ten prototyp używa standardowej biblioteki Pythona.

## 2. Pierwszy test

W drugim terminalu Pi 5:

```bash
curl -sS http://10.77.77.1:8000/health
python3 ~/EWA_Brain/chat_cli.py
```

Przykładowy scenariusz (wpisuj po jednej wypowiedzi):

```
Kim jesteś?
Do projektu SmartRoom dodałem czujnik temperatury.
Co wiesz o SmartRoom?
Podaj stan small rum.
Tak
Chyba SmartRoom wykorzystuje czujnik ruchu.
Nie
/exit
```

Pamiętaj: to test języka i pamięci — **E.W.A. nie potrafi jeszcze faktycznie odczytać statusu MQTT**. Model ma o tym powiedzieć, a nie wymyślać status.

Dla lepszych wyników sprawdzaj osobno nowe wiadomości (pamięć ostatnich 8 wypowiedzi) i zapamiętane fakty (`projects.json`). Model Qwen2.5 3B jest niewielki; nie zawsze prawidłowo sklasyfikuje wypowiedź — dlatego decyzję o zapisie podejmuje kod Pythona.

## 3. Jak działa tryb hybrydowy?

- **Nowa, jednoznaczna informacja o *znanym* projekcie**: Qwen proponuje `add_fact`; Python sprawdza, czy wypowiedź rzeczywiście wymienia projekt i przekazuje dany fakt. Jeśli tak, zapisuje informację automatycznie.
- **Informacja niepewna** (np. „chyba”), **nowy projekt** lub **korekta istniejącej informacji**: E.W.A. pyta o potwierdzenie. „Tak” zapisuje, „nie” odrzuca. Pytanie pozostaje oczekujące po restarcie.
- **Pomyłka STT** (np. „small rum”): gdy Qwen zapyta „Czy chodzi Ci o SmartRoom?”, odpowiedź „tak” pozwala automatycznie zapisać *potwierdzony* alias, ale tylko jeśli wariant nazwy naprawdę był w wypowiedzi.
- **Zwykłe pytania, prośby o włączenie urządzenia** i **wywnioskowane przez model fakty** nie powinny automatycznie zmieniać bazy. Bez odpowiedniego modułu Pi 3 żadne polecenie MQTT nie jest wykonywane.

Zapis faktów korzysta z zabezpieczeń heurystycznych. Nie jest to jeszcze w pełni niezawodna weryfikacja faktów; pod kontrolą powinno pozostawać `projects.json` i dziennik zmian.

## 4. Gdzie znajdują się dane?

```
~/EWA_Brain/
├── config/
│   ├── Modelfile                 # Twój istniejący plik (nie nadpisywany)
│   └── brain_rules.txt           # dodatkowe instrukcje serwera, zmiany bez ollama create
├── memory/
│   ├── projects.example.json     # tylko początkowy szablon
│   ├── projects.json             # trwała wiedza i aliasy; generowany przy pierwszym starcie
│   ├── projects.json.bak         # automatyczna kopia poprzedniej wersji
│   └── conversations.db          # historia, potwierdzenia, audyt zmian
├── modules/
│   ├── brain.py
│   └── memory_manager.py
├── server.py                     # API na USB: /health, /chat
├── chat_cli.py                   # rozmowa terminalowa z serwerem
├── manage_memory.py             # podgląd danych
├── ewa-brain.service
└── tests/
```

Dodatkowe instrukcje modyfikujesz w `~/EWA_Brain/config/brain_rules.txt`. Są wczytywane przy każdym zapytaniu. Nie wymagają ponownego `ollama create ewa`. Zmiany `Modelfile` nadal wymagają `ollama create ewa -f ~/EWA_Brain/config/Modelfile`.

Podgląd danych:

```bash
cd ~/EWA_Brain
python3 manage_memory.py projects
python3 manage_memory.py history --limit 20
python3 manage_memory.py events --limit 20
python3 manage_memory.py pending
```

Każda zaakceptowana zmiana wiedzy ma wpis w `memory_events` w SQLite. Przed zapisem JSON tworzona jest kopia `projects.json.bak` (jedna poprzednia wersja). Żeby ją przywrócić, **zatrzymaj serwer**, skopiuj `.bak` w miejsce `.json` i uruchom ponownie. Kopia `.bak` może być nieaktualna, jeśli ktoś zmieniał plik ręcznie.

Do pełnej kopii zapasowej, po zatrzymaniu serwera, skopiuj `memory/projects.json` oraz `memory/conversations.db`.

## 5. Test przez USB z Pi 3

Po uruchomieniu serwera na Pi 5, na Pi 3:

```bash
curl -sS http://10.77.77.1:8000/health
curl -sS http://10.77.77.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"ewa-main","text":"Co wiesz o SmartRoom?"}'
```

Odpowiedź ma postać:

```json
{"session_id":"ewa-main","type":"answer","text":"...","memory":"none"}
```

`type` ma wartość `answer` albo `clarify`; `memory` może też mieć wartość `saved`, `pending_confirmation`, `rejected`, `skipped`. Przekazuj zawsze ten sam `session_id` podczas jednej rozmowy.

## 6. Autostart — dopiero po testach ręcznych

Zatrzymaj testowy serwer (`Ctrl+C`), następnie:

```bash
sudo cp ~/EWA_Brain/ewa-brain.service /etc/systemd/system/ewa-brain.service
sudo systemctl daemon-reload
sudo systemctl enable --now ewa-brain
sudo systemctl status ewa-brain --no-pager
```

Logi i reset:

```bash
journalctl -u ewa-brain -n 60 --no-pager
sudo systemctl restart ewa-brain
```

Serwis korzysta z `User=soul`, `/home/soul/EWA_Brain`, `EVA_MODEL=ewa` oraz `EVA_USB_IP=10.77.77.1`. Zmień je, jeżeli masz inną konfigurację. Jeżeli po restarcie interfejs USB otrzymuje adres z opóźnieniem, `systemd` będzie ponawiał start procesu, aż interfejs będzie dostępny.

## 7. Bezpieczeństwo i ograniczenia

- Ollama pozostaje przy `http://127.0.0.1:11434` (nie otwieraj jej API na świat).
- Serwer E.W.A. wiąże się domyślnie **wyłącznie** z adresem USB `10.77.77.1:8000`. Nie ustawiaj `0.0.0.0` bez zabezpieczeń.
- Prototyp nie ma jeszcze uwierzytelniania HTTP. **Przed dodaniem funkcji sterujących MQTT trzeba dodać uwierzytelnianie i listę dozwolonych działań**.
- Nie zapisuj haseł i tokenów w `projects.json`. Walidacja blokuje część oczywistych sekretów, lecz nie wszystkie możliwe przypadki.
- JSON z projektu jest traktowany jako dane kontekstowe, nie jako instrukcje modelu. LLM nie ma dowolnego dostępu do plików.
- Zależnie od obciążenia Pi 5 generowanie odpowiedzi Qwena na CPU może potrwać kilkanaście lub kilkadziesiąt sekund.
- Testy z paczki wykonano z **symulowanymi odpowiedziami Qwena**. Zachowanie faktycznego modelu na Twoim Pi należy sprawdzić powyższymi rozmowami; nie ma gwarancji, że zawsze poprawnie wyodrębni wszystkie nowe informacje.
