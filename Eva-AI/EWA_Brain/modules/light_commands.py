"""Interpretacja poleceń LED; model wybiera działanie, a kod sprawdza parametry."""
from __future__ import annotations

import re

from .calculator import integer
from .memory_manager import normal

TARGETS = {
    "bed": {"lozko", "lozka", "lozku", "przy lozku", "nad lozkiem", "pod lozkiem"},
    "shelf": {"polka", "polke", "polki", "polce", "na polce",
              "szafka", "szafke", "szafki", "szafce", "na szafce"},
}
ALL_TARGETS = {"wszystkie", "wszystkich", "wszystkim", "wszystko", "oba", "obie", "obu", "obydwa", "obydwie", "obydwu",
               "wszedzie", "w calym pokoju", "caly pokoj", "wszystkie w pokoju", "oba w pokoju"}
LIGHT_WORDS = r"\b(?:ledy|led|ledow|ledami|swiatlo|swiatla|swiatel|oswietlenie|oswietlenia|tasme|tasmy|tasma|obwody|obwod|obwodow)\b"
ACTIONS = ("power", "brightness", "adjust_brightness", "status")

# Krótkie pola tekstowe zachowują zgodność ze starszym kontraktem Brain.
LIGHT_PROPERTIES = {
    "light_action": {"type": "string", "enum": ["none", *ACTIONS]},
    "light_target": {"type": "string", "enum": ["", "bed", "shelf", "all"]},
    "light_value": {"type": "string"},
}
LIGHT_RULES = (
    "NARZĘDZIE LED jest dostępne przez kod serwera. Zastępuje to starszą informację o braku MQTT. "
    "Urządzenia: bed=LED-y przy łóżku, shelf=LED-y na półce/szafce, all=oba obwody. "
    "Rozpoznawaj sens wypowiedzi i kontekst ostatniego polecenia, także odmiany nazw i błędy STT. "
    "Dla prośby o sterowanie dodaj light_action: power, brightness, adjust_brightness lub status; "
    "light_target: bed, shelf, all albo pusty tekst, gdy nie wiadomo. "
    "light_value jest tekstem: dla power '1'/'0', brightness procent 0–100, "
    "adjust_brightness zmiana w punktach procentowych -100–100, status pusty tekst. "
    "Bez podanej wielkości 'trochę jaśniej/ciemniej' oznacza +10/-10 punktów. "
    "Gdy brakuje jasności, zostaw light_value pusty. "
    "Przy niepewnym dopasowaniu urządzenia użyj type=clarify z propozycją w polach light_. "
    "Nie przypisuj nowego miejsca do znanego obwodu bez doprecyzowania. "
    "'Czy światło jest włączone?' to status. Porady, opisy, negacje, hipotezy i przyszłe "
    "polecenia nie są sterowaniem. Nie łącz kilku różnych działań w jedno. "
    "Nie twierdź, że wykonałaś polecenie: odpowiedź o wykonaniu tworzy kod po odczycie MQTT. "
    "Przy zwykłej rozmowie pomiń pola light_. Polecenia LED nie zmieniają pamięci projektów."
)


def normalize(text):
    return normal(text).replace("ł", "l").replace("led-y", "ledy").replace("led-ow", "ledow").strip(" .!?,")


def request_text(text):
    text = normalize(text)
    text = re.sub(r"^(?:ewa|eva)[ ,]+", "", text)
    text = re.sub(r"^(?:czy )?(?:mozesz|moglabys|moglbys)\s+", "", text)
    text = re.sub(r"^prosze(?: cie)?[ ,]+", "", text)
    infinitives = {"wlaczyc": "wlacz", "wylaczyc": "wylacz", "zapalic": "zapal", "zgasic": "zgas",
                   "ustawic": "ustaw", "rozjasnic": "rozjasnij", "przyciemnic": "przyciemnij"}
    first, space, rest = text.partition(" ")
    text = infinitives.get(first, first) + space + rest
    return re.sub(r"[, ]+prosze$", "", text)


def target_from(text):
    text = normalize(text)
    text = " ".join(re.sub(LIGHT_WORDS, "", text).split())
    if text in ALL_TARGETS:
        return "all"
    for key, variants in TARGETS.items():
        if text in variants:
            return key
    parts = re.split(r" (?:i|oraz) ", text)
    if len(parts) == 2 and {target_from(p) for p in parts} == {"bed", "shelf"}:
        return "all"
    return None


def mentioned_targets(text):
    """Tylko całe nazwy/aliasy; 'półka' nie pasuje np. do 'półkaźnik'."""
    text = normalize(text)
    targets = {key for key, names in TARGETS.items()
               if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text) for name in names)}
    if len(targets) == 2 or re.search(r"\b(?:wszystkie|wszystkich|wszystkim|wszystko|oba|obie|obu|obydwa|obydwie|obydwu|wszedzie)\b|w calym pokoju|caly pokoj", text):
        return {"all"}
    return targets


def can_change_lights(text):
    """AI nie może zmienić opisu, negacji ani pytania o sposób działania w komendę."""
    text = request_text(text)
    if re.search(r"\b(?:nie|nigdy|jesli|jezeli|gdy|gdyby|gdybym|jak|kiedy|jutro|pozniej|potem|moze|wieczorem|rano|dopiero)\b|"
                 r"za (?:\d+|chwile|minute|godzine)\b|o (?:godzinie |\d{1,2}:\d{2}\b)|[\"'„”]", text):
        return False
    if re.match(r"^(?:wlacz|wylacz|zapal|zgas|zaswiec)\b", text) and re.search(r"\bo \d{1,2}\b", text):
        return False
    if len(re.findall(r"\b(?:wlacz|wylacz|zapal|zgas|ustaw|rozjasnij|przyciemnij|sciemnij)\b", text)) > 1:
        return False
    return bool(re.match(r"^(?:wlacz|wylacz|zapal|zgas|zaswiec|odpal|uruchom|ustaw|zmien|"
                         r"rozjasnij|przyciemnij|sciemnij|zwieksz|zmniejsz|podswietl|oswietl|"
                         r"rozswietl|daj|zrob|(?:troche )?(?:jasniej|ciemniej))\b", text))


def percent_from(text):
    text = re.sub(r"\s*(?:procent(?:ow|y)?|%)$", "", normalize(text)).strip()
    shortcuts = {"polowa": 50, "polowe": 50, "pol": 50, "pol jasnosci": 50,
                 "maksimum": 100, "maksymalna": 100, "maks": 100, "minimum": 0}
    value = shortcuts[text] if text in shortcuts else integer(text.split())
    if not 0 <= value <= 100:
        raise ValueError("Jasność musi mieścić się w zakresie 0–100 procent.")
    return value


def raw_brightness(percent):
    return (percent * 255 + 50) // 100


def parse_command(text):
    text = request_text(text)
    action, value, target = None, None, ""
    match = re.fullmatch(r"(?:podaj stan|pokaz stan|sprawdz stan|jaki jest stan|stan|"
                         r"podaj jasnosc|sprawdz jasnosc|jaka jest jasnosc)(?: (.+))?", text)
    question = re.fullmatch(r"czy (.+?) (?:jest|sa) (?:wlaczone|wlaczony|wlaczona|wylaczone|wylaczony|wylaczona)", text)
    if match or question:
        action, target = "status", (match or question).group(1) or ""
    elif not can_change_lights(text):
        return None
    else:
        match = re.fullmatch(r"(wlacz|wylacz|zapal|zgas|zaswiec|odpal|uruchom)(?: mi)? (.+)", text)
        if match:
            action, value, target = "power", int(match[1] not in ("wylacz", "zgas")), match[2]
        else:
            match = re.fullmatch(r"(?:ustaw|zmien) (?:jasnosc|intensywnosc(?: oswietlenia)?)(?: (.*?))? na (.+)", text)
            if match:
                target = match[1] or ""
                try:
                    value = raw_brightness(percent_from(match[2]))
                except ValueError:
                    if not target and target_from("na " + match[2]):
                        target = "na " + match[2]
                    else:
                        return {"error": "Podaj jasność od zera do stu procent."}
                action = "brightness"
            else:
                match = re.fullmatch(r"(?:ustaw|zmien) (?:jasnosc|intensywnosc(?: oswietlenia)?)(?: (.+))?", text)
                if match:
                    action, target = "brightness", match[1] or ""
                else:
                    match = re.fullmatch(r"(rozjasnij|przyciemnij|sciemnij|zwieksz jasnosc|zmniejsz jasnosc)(?: (.*))?", text)
                    short = re.fullmatch(r"(?:troche )?(jasniej|ciemniej)(?: (.+))?", text)
                    if not match and not short:
                        return None
                    verb, rest = (match or short).groups()
                    rest = rest or ""
                    amount = re.fullmatch(r"(?:(.*?) )?o (.+)", rest)
                    target, step = rest, 10
                    if amount:
                        target = amount[1] or ""
                        try:
                            step = percent_from(amount[2])
                        except ValueError:
                            return {"error": "Podaj zmianę jasności w procentach, np. o dziesięć procent."}
                    action = "adjust_brightness"
                    value = -step if verb in ("przyciemnij", "sciemnij", "zmniejsz jasnosc", "ciemniej") else step
    device = target_from(target)
    generic = normalize(target) in {"", "ledy", "led", "ledow", "swiatlo", "swiatla", "oswietlenie",
                                  "ledy w pokoju", "swiatlo w pokoju", "oswietlenie w pokoju"}
    if device is None and not generic:
        # Nietypowe sformułowanie trafia do AI razem z katalogiem dostępnych urządzeń.
        return None
    return {"action": action, "target": device, "value": value}


def command_from_ai(response):
    action = response.get("light_action", "none")
    if action in ("none", ""):
        return None
    target, value = response.get("light_target", ""), response.get("light_value", "")
    if action not in ACTIONS or target not in ("", "bed", "shelf", "all") or not isinstance(value, str):
        raise ValueError("Niepoprawne działanie lub urządzenie LED z AI")
    if action == "status":
        if value:
            raise ValueError("Odczyt stanu LED nie przyjmuje wartości")
        number = None
    elif not value:
        number = None
    else:
        if not re.fullmatch(r"-?[0-9]{1,3}", value):
            raise ValueError("Niepoprawna wartość LED z AI")
        number = int(value)
        minimum, maximum = (-100, 100) if action == "adjust_brightness" else (0, 1 if action == "power" else 100)
        if not minimum <= number <= maximum:
            raise ValueError("Wartość LED z AI poza zakresem")
        if action == "brightness":
            number = raw_brightness(number)
    return {"action": action, "target": target or None, "value": number}


def command_description(command):
    label = {"bed": "przy łóżku", "shelf": "na półce / szafce", "all": "na półce i przy łóżku"}[command["target"]]
    action, value = command["action"], command["value"]
    if action == "power":
        return f"{'włączyć' if value else 'wyłączyć'} LED-y {label}"
    if action == "brightness":
        return f"ustawić jasność LED-ów {label} na około {round(value * 100 / 255)} procent"
    if action == "adjust_brightness":
        return f"{'zwiększyć' if value >= 0 else 'zmniejszyć'} jasność LED-ów {label} o {abs(value)} punktów procentowych"
    return f"sprawdzić stan LED-ów {label}"
