"""Proste działania po polsku, liczone dokładnie bez modelu i bez eval()."""
from __future__ import annotations

import ast
import operator
import re
from decimal import Decimal, localcontext
from fractions import Fraction

from .memory_manager import normal

UNITS = dict(zip("zero jeden dwa trzy cztery piec szesc siedem osiem dziewiec".split(), range(10)))
TEENS = dict(zip(("dziesiec jedenascie dwanascie trzynascie czternascie pietnascie "
                  "szesnascie siedemnascie osiemnascie dziewietnascie").split(), range(10, 20)))
TENS = dict(zip("dwadziescia trzydziesci czterdziesci piecdziesiat szescdziesiat siedemdziesiat osiemdziesiat dziewiecdziesiat".split(), range(20, 100, 10)))
HUNDREDS = dict(zip("sto dwiescie trzysta czterysta piecset szescset siedemset osiemset dziewiecset".split(), range(100, 1000, 100)))
NUMBER_WORDS = set(UNITS) | set(TEENS) | set(TENS) | set(HUNDREDS) | {"przecinek"}
OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
TOKEN = re.compile(r"[0-9]+(?:[.,][0-9]+)?|[a-z]+|[+*/()\-]")
PREFIX = re.compile(r"^(?:(?:prosze )?(?:ile (?:to|tu|jest|wynosi)|oblicz|policz|jaki jest wynik(?: dzialania)?))\s*:?[ ]*")
MAX_VALUE = 10 ** 12


class CalculationLimit(ValueError):
    pass


def integer(words):
    """Liczebnik 0–999; błędne ciągi typu 'trzy trzy' nie są dodawane."""
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    words = list(words)
    value = HUNDREDS.get(words[0], 0) if words else 0
    if value:
        words.pop(0)
    if words and words[0] in TENS:
        value += TENS[words.pop(0)]
        if words and words[0] in UNITS and UNITS[words[0]] > 0:
            value += UNITS[words.pop(0)]
    elif words and words[0] in TEENS:
        value += TEENS[words.pop(0)]
    elif words and words[0] in UNITS:
        number = UNITS[words.pop(0)]
        if number == 0 and value:
            raise ValueError("zero po setkach")
        value += number
    elif not value:
        raise ValueError("brak liczby")
    if words:
        raise ValueError("niejednoznaczny liczebnik")
    return value


def number(parts):
    if len(parts) == 1 and re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", parts[0]):
        value = parts[0].replace(",", ".")
    elif "przecinek" in parts:
        index = parts.index("przecinek")
        whole = integer(parts[:index])
        tail = parts[index + 1:]
        if not tail:
            raise ValueError("brak części ułamkowej")
        if len(tail) == 1 and tail[0].isdigit():
            fraction = tail[0]
        elif all(word in UNITS for word in tail):
            fraction = "".join(str(UNITS[word]) for word in tail)
        else:
            fraction = str(integer(tail))
        value = f"{whole}.{fraction}"
    else:
        value = str(integer(parts))
    whole, dot, tail = value.partition(".")
    if len(whole) > 13 or len(tail) > 12 or Fraction(value) > MAX_VALUE:
        raise CalculationLimit()
    # Python nie akceptuje 007 jako literału całkowitego.
    return str(int(whole)) + (dot + tail if dot else "")


def expression_from(text):
    text = normal(text).replace("ł", "l").strip(" .!?")
    text = PREFIX.sub("", text, count=1)
    text = text.translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
    for phrase, symbol in ((r"(?:pomnozone|mnozone) przez|razy", "*"),
                           (r"(?:podzielone|dzielone) przez", "/"),
                           (r"plus", "+"), (r"minus", "-")):
        text = re.sub(r"\b(?:" + phrase + r")\b", symbol, text)
    tokens = []
    end = 0
    for match in TOKEN.finditer(text):
        if text[end:match.start()].strip():
            return None
        token = match.group()
        if token.isalpha() and token not in NUMBER_WORDS:
            return None
        tokens.append(token)
        end = match.end()
    if text[end:].strip() or not any(token in "+-*/" for token in tokens):
        return None
    if len(tokens) > 120 or tokens.count("(") > 12:
        raise CalculationLimit()
    result, words = [], []
    for token in [*tokens, "END"]:
        if token in ("+", "-", "*", "/", "(", ")", "END"):
            if words:
                result.append(number(words))
                words = []
            if token != "END":
                result.append(token)
        else:
            words.append(token)
    return " ".join(result)


def evaluate(expression):
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    # Walidacja całego drzewa przed pierwszym obliczeniem, także przed dzieleniem przez zero.
    if any(not isinstance(node, allowed) for node in nodes):
        raise ValueError("nieobsługiwany zapis działania")
    if sum(isinstance(node, (ast.BinOp, ast.UnaryOp)) for node in nodes) > 32:
        raise CalculationLimit()
    if not any(isinstance(node, ast.BinOp) for node in nodes):
        raise ValueError("brak działania na dwóch liczbach")

    def visit(node):
        if isinstance(node, ast.Constant):
            # Odczyt oryginalnego zapisu zachowuje dokładność liczb dziesiętnych.
            value = Fraction(ast.get_source_segment(expression, node))
        elif isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.USub):
                value = -value
        else:
            value = OPERATIONS[type(node.op)](visit(node.left), visit(node.right))
        if abs(value) > MAX_VALUE or max(value.numerator.bit_length(), value.denominator.bit_length()) > 256:
            raise CalculationLimit()
        return value

    return visit(tree.body)


def answer_text(value):
    if value.denominator == 1:
        return f"Wynik to {value.numerator}."
    with localcontext() as context:
        context.prec = 50
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        rendered = format(decimal, ".6f").rstrip("0").rstrip(".")
    if Fraction(rendered) == 0 and value != 0:
        return f"Dokładny wynik to {value.numerator} podzielone przez {value.denominator}."
    qualifier = "" if Fraction(rendered) == value else "około "
    return f"Wynik to {qualifier}{rendered.replace('.', ',')}."


def calculate(text):
    """None: zwykła rozmowa. Słownik: wynik albo prośba o poprawienie działania."""
    if not isinstance(text, str) or not 1 <= len(text) <= 600:
        return None
    expression = None
    try:
        expression = expression_from(text)
        if expression is None:
            return None
        message = answer_text(evaluate(expression))
        kind = "answer"
    except ZeroDivisionError:
        message, kind = "Nie można dzielić przez zero.", "answer"
    except CalculationLimit:
        message, kind = "To działanie przekracza limit prostego kalkulatora. Podaj mniejsze liczby lub krótsze działanie.", "clarify"
    except (ValueError, SyntaxError):
        message, kind = "Nie odczytałam jednoznacznego działania. Podaj liczby oraz plus, minus, razy lub dzielone przez.", "clarify"
    return {"type": kind, "text": message, "source": "calculator", "expression": expression or ""}
