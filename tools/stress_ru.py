"""Русские ударения для синтеза: словарь RUAccent + юникод-знак ударения.

Словарь хранит слова с плюсом перед ударной гласной («догов+ор»). Для движка
плюс не годится (он читает его как слово «плюс»), поэтому плюс превращаем в
комбинируемый знак ударения U+0301 после ударной гласной — он проверен на слух
и звучание меняет.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from pathlib import Path

# Каталог RUAccent можно задать переменной среды; по умолчанию — папка Speech Studio.
RUACCENT_DIR = Path(
    os.environ.get(
        "RUACCENT_DIR",
        r"C:\Users\ipdnv\AppData\Local\Speech Studio\models\ruaccent\dictionary",
    )
)
DICT_PATH = RUACCENT_DIR / "accents.json.gz"
YO_PATH = RUACCENT_DIR / "yo_words.json.gz"

VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
WORD_RE = re.compile(r"[А-Яа-яЁё]+")
_accent: dict[str, str] | None = None
_yo: dict[str, str] | None = None


def _load() -> None:
    global _accent, _yo
    if _accent is not None:
        return
    with gzip.open(DICT_PATH, "rt", encoding="utf-8") as handle:
        _accent = json.load(handle)
    try:
        with gzip.open(YO_PATH, "rt", encoding="utf-8") as handle:
            _yo = json.load(handle)
    except Exception:  # noqa: BLE001
        _yo = {}


def _plus_to_accent(word: str) -> str:
    """«догов+ор» → «догово́р» (знак после ударной гласной)."""
    if "+" not in word:
        return word
    result = []
    stressed = False
    for ch in word:
        if ch == "+":
            stressed = True
            continue
        result.append(ch)
        if stressed and ch in VOWELS:
            result.append("\u0301")
            stressed = False
    return "".join(result)


def add_stress(text: str) -> str:
    """Ставит ударения в словах, которые есть в словаре, и возвращает текст."""
    _load()
    assert _accent is not None

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        lower = word.lower()
        if _yo:
            with_yo = _yo.get(lower)
            if with_yo:
                return with_yo
        stressed = _accent.get(lower)
        if not stressed:
            return word
        # Сохраняем регистр первой буквы, если слово было с большой.
        result = _plus_to_accent(stressed)
        if word[0].isupper():
            result = result[0].upper() + result[1:]
        return result

    return WORD_RE.sub(replace, text)


if __name__ == "__main__":
    sample = "Николай, включи договор и скажи, который час. Красивее звонИт или звонит?"
    print(add_stress(sample))
