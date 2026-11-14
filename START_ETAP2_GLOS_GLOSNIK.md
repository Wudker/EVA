# E.W.A. — etap 2: mikrofon → AI → tekst i głośnik

Paczka jest aktualizacją repozytorium Wudker/EVA, przygotowaną na bazie
commitu `f0b66a260b714856c287092064ccc2fd54761416` (14.11.2026).
Nie zawiera modeli, prywatnej pamięci ani ustawień sprzętu.

Pi3 nagrywa i rozpoznaje mowę przez Vosk. Pi5 odpowiada przez istniejące
`/chat`. Pi3 wyświetla odpowiedź i, z `--speak`, odczytuje ją przez głośnik.
Głos eSpeak NG jest prosty i syntetyczny; pracuje lokalnie. Najpierw sprawdzamy
cały tor, a później można wymienić syntezator na przyjemniejszy głos.

## 1. Wgranie paczki do GitHuba

Rozpakuj pliki z ZIP do głównego katalogu lokalnego repo `EVA`, zachowując
strukturę: `Eva-hand/`, `instalation/` i ten plik obok siebie. Nadpisz wersje
plików wymienione w `PACZKA_MANIFEST.json`. Jeśli edytowałeś je lokalnie od
wskazanego commitu, porównaj zmiany przed nadpisaniem. Następnie wykonaj
commit i push we własnym repozytorium. Paczka nie została automatycznie wysłana na GitHuba.

Na obu Pi pobierz aktualizację:

```bash
cd ~/EVA
git pull --ff-only
```

Jeśli Git zgłasza lokalne zmiany, zatrzymaj się i zachowaj je; nie używaj
`reset --hard` do obejścia konfliktu.

## 2. Działająca Pi3 — doinstaluj tylko mowę

Twoja Pi3 ma już działające STT, dlatego wystarczy:

```bash
cd ~/EVA
bash instalation/install_pi3.sh --tts-only
python3 Eva-hand/voice_client.py --list-outputs
python3 Eva-hand/voice_client.py --tts-test
```

Powinno być słychać „Cześć Bartłomieju. Tu Ewa. Test głośnika działa.”
Ten test nie używa mikrofonu, Vosk, AI ani połączenia USB.

Jeśli dźwięku nie ma, `default` może wskazywać inne wyjście (np. HDMI).
Wybierz urządzenie z wyniku `--list-outputs`; poniżej `NAZWA_KARTY` to
miejsce do zastąpienia rzeczywistą nazwą z listy:

```bash
python3 Eva-hand/voice_client.py --tts-test --output-device 'plughw:CARD=NAZWA_KARTY,DEV=0'
```

Sprawdź również wyciszenie i poziom wyjścia w `alsamixer` (F6 wybiera kartę).
Program nie przestawia miksera i nie zmienia dotychczasowego urządzenia nagrywającego.
Nazwy kart są stabilniejsze niż numery w rodzaju `hw:1,0`.

## 3. Test pytania tekstowego z odpowiedzią głosową

```bash
cd ~/EVA
python3 Eva-hand/voice_client.py --text 'Ile to dwa plus dwa?' --speak
```

Jeżeli w poprzednim teście potrzebowałeś `--output-device`, dodaj go tutaj
albo zapisz ustawienia z punktu 4. Najpierw pojawi się odpowiedź tekstowa,
następnie zostanie odczytana. Błąd głośnika nie ponawia pytania do AI.

## 4. Zapamiętanie urządzeń audio — na Pi3

```bash
cd ~/EVA
cp -n Eva-hand/audio.example.json Eva-hand/audio.local.json
nano Eva-hand/audio.local.json
```

`device` to dotychczasowe wejście mikrofonu; `output_device` to sprawdzone
wyjście głośnika. Pozostaw `default`, jeśli testy działają. Możesz sprawdzić wejścia:

```bash
python3 Eva-hand/voice_client.py --list-devices
```

`speak: true` włącza odczyt, `seconds` to długość nagrania, a `speech_rate`
to tempo mowy (80–300, domyślnie 160). `audio.local.json` nie trafia do Gita.
Na kolejnych uruchomieniach plik wczytuje się automatycznie, niezależnie od katalogu terminala.

## 5. Pełny tor mikrofon → AI → głośnik

```bash
cd ~/EVA
Eva-hand/.venv/bin/python Eva-hand/voice_client.py --speak
```

Enter rozpoczyna jedno nagranie. Potem program zwalnia mikrofon, rozpoznaje
mowę, czeka na Pi5 i odczytuje odpowiedź. Następne nagranie wymaga kolejnego
Entera. `q` + Enter kończy program; Ctrl+C przerywa również odtwarzanie.

Przykładowo powiedz „Ile to dwa plus dwa?”, a potem „Co wiesz o SmartRoom?”.
Pole `Ty:` pokazuje wynik STT, więc można odróżnić błąd rozpoznania od błędu AI.
Program wyświetla czas STT, oczekiwania na API, syntezy i odtwarzania.

To nadal nagranie o zadanej długości, bez automatycznego wykrywania ciszy,
słowa wybudzającego i przerywania głosu kolejną wypowiedzią.
W tej paczce nie zmieniono modelu ani logiki Pi5 — czas odpowiedzi AI może
pozostać podobny do dotychczasowych około 70 sekund.

## 6. Kopia Pi5 — zrób po udanym teście

Na Pi5, po pobraniu aktualizacji repo:

```bash
cd ~/EVA
bash instalation/backup_pi5.sh
```

Skrypt na krótko zatrzymuje usługę mózgu, tworzy kopię w `~/ewa-backups`
i przywraca poprzedni stan usługi. Obejmuje pamięć projektów, bazę rozmów,
lokalny Modelfile i zasady mózgu. Szczegóły przywracania są w
[`instalation/README.md`](instalation/README.md).

Skopiuj kopię na komputer — w PowerShellu:

```powershell
scp -r soul@eva-ai.local:/home/soul/ewa-backups .
```

Kopia na tej samej karcie SD nie chroni przed awarią karty. Archiwum zawiera
prywatną pamięć: zachowaj je na komputerze, poza publicznym repo.

## 7. Diagnostyka

```bash
# Na Pi3:
python3 instalation/doctor.py --role pi3

# Na Pi5:
python3 instalation/doctor.py --role pi5

# Opcjonalny pomiar jednego zapytania do AI, z dowolnej z malinek:
python3 instalation/doctor.py --role pi5 --chat
```

Na Pi3 ostatni przykład uruchom z `--role pi3`. `--chat` tworzy jedną
testową wymianę w historii sesji `ewa-diagnostic`; bez tej flagi diagnostyka
nie wysyła pytania do modelu i nie zmienia konfiguracji.

## 8. Proponowany następny etap — pierwsze działanie SmartRoom

Po udanym teście głośnika proponuję jedną funkcję: „włącz/wyłącz LED półki”.
Najpierw konkretna komenda i sprawdzony rezultat, później jasność i efekty.

Zweryfikowano kod `Wudker/SmartRoom` w commicie
`3fbf349f395f03e274a2c3cd8609ce7963fbc54f`:

| Funkcja | Temat MQTT | Wartość |
| --- | --- | --- |
| Sterowanie półką | `smartroom/workshop/led_shelf/set` | `1` / `0` |
| Stan półki | `smartroom/workshop/led_shelf/state` | `1` / `0` |
| Zadana jasność półki | `smartroom/workshop/led_shelf/brightness/set` | `0`–`255` |
| Stan jasności półki | `smartroom/workshop/led_shelf/brightness/state` | `0`–`255` |
| Dostępność sterownika | `smartroom/workshop/led_controller/availability` | `online` / `offline` |

Uwaga praktyczna: `README_topics.txt` w SmartRoom opisuje starsze nazwy
jasności i dostępności. Tabela powyżej pochodzi z `MQTT.cpp`; przed
integracją trzeba potwierdzić, że właśnie ta wersja działa na ESP32.

Plan realizacji: rozpoznany zamiar → dozwolona funkcja w Pythonie → MQTT
→ sprawdzenie stanu sterownika → odpowiedź tekstowa/głosowa.
LLM nie powinien dobierać dowolnego tematu ani uruchamiać poleceń systemowych.
Proste komendy LED można obsłużyć bez oczekiwania na generowanie przez Qwena.

Publikacja do brokera nie potwierdza wykonania przez ESP32. Kod sterownika
publikuje stan przy zmianie; dla stanu już zgodnego z żądaniem trzeba zwrócić
„już włączone”, a nie czekać bez końca na nowy komunikat. Zapamiętany stan
retained sam w sobie nie jest świeżym potwierdzeniem nowego działania.
Przed dodaniem sterowania trzeba też ustalić dostęp do brokera i uwierzytelnienie
API E.W.A. Dane logowania zostają w lokalnej konfiguracji poza repo.

Kolejność po tym etapie: prosty LED → pomiar/optymalizacja opóźnień AI →
wykrywanie końca mowy → słowo wybudzające → autostart klienta głosowego.
Obecny program wymaga Entera, więc instalator nie dodaje dla niego usługi systemd.

## Źródła techniczne i zakres weryfikacji

- [eSpeak NG: argumenty i generowanie WAV](https://github.com/espeak-ng/espeak-ng/blob/master/src/espeak-ng.1.ronn)
- [Vosk: instalacja](https://alphacephei.com/vosk/install)
- [Ollama na Linux](https://docs.ollama.com/linux)
- [NetworkManager: nmcli](https://networkmanager.dev/docs/api/latest/nmcli.html)
- [SmartRoom: MQTT.cpp](https://github.com/Wudker/SmartRoom/blob/3fbf349f395f03e274a2c3cd8609ce7963fbc54f/Software/SmartRoom_Led_Strip/src/MQTT.cpp)

Testy wykonano w środowisku Linux bez fizycznych Raspberry Pi, ReSpeakera,
Ollamy ani eSpeak NG. Testy audio uruchamiają procesy zastępujące syntezator
i odtwarzacz; rzeczywistego brzmienia i wyjścia audio nie zweryfikowano.
Instalatory sprawdzono składniowo i w trybie `--dry-run`; pobieranie pakietów,
działanie usług oraz sprzęt wymagają powyższych testów na malinkach.

Wynik: **44 testy zakończone powodzeniem** — 22 klienta/audio, 9 instalacji
i kopii, 13 dotychczasowego mózgu. Dodatkowo sprawdzono składnię plików Python
i zakończenia linii LF w skryptach.
