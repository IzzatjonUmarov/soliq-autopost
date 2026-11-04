"""Узбекский текст на латинице в НОВОЙ редакции алфавита (по образцу uz-business-news).

Закон № ЗРУ-1184 от 21.10.2026: вместо Sh, Ch, Oʻ, Gʻ — буквы Ş, Ç, Ö, Ğ;
апостроф ʼ — только вместо твёрдого знака (ta’lim → taʼlim).
Любой текст (кириллица, старая латиница, смесь) приводится к новому алфавиту функцией to_new_latin.
"""

from __future__ import annotations

import html
import re

TUTUQ = "ʼ"

CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "s", "ч": "ç", "ш": "ş", "щ": "ş", "ъ": TUTUQ, "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ў": "ö", "қ": "q", "ғ": "ğ", "ҳ": "h",
}
VOWELS = set("аеёиоуўэюяaeiou")

QUOTES = "‘’ʻʼ'`´"
OLD_O = re.compile(rf"([Oo])[{QUOTES}]")
OLD_G = re.compile(rf"([Gg])[{QUOTES}]")
SEPARATED_SH = re.compile(rf"([Ss])[{QUOTES}]([Hh])")  # Is’hoq → Ishoq (s и h раздельно)
OTHER_QUOTE = re.compile(rf"(?<=\w)[{QUOTES}](?=\w)")
# Иностранные слова и бренды не трогаем: Visa, Mastercard, Click, Cashback
FOREIGN = re.compile(r"[wW]|[cC](?![hH])|sch|ph|th|ck|ee|oo", re.I)


def cyr_to_latin(text: str) -> str:
    out = []
    for i, ch in enumerate(text):
        low = ch.lower()
        if low == "ц":
            prev = text[i - 1].lower() if i else " "
            lat = "ts" if prev in VOWELS else "s"
        elif low == "е":
            prev = text[i - 1].lower() if i else " "
            lat = "ye" if (not prev.isalpha() or prev in VOWELS or prev in "ъь") else "e"
        elif low in CYR_TO_LAT:
            lat = CYR_TO_LAT[low]
        else:
            out.append(ch)
            continue
        if ch.isupper() and lat:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            lat = lat.upper() if (nxt.isupper() and len(lat) > 1) else lat[0].upper() + lat[1:]
        out.append(lat)
    return "".join(out)


def _word_to_new(word: str) -> str:
    if FOREIGN.search(word.replace("ç", "").replace("ş", "")):
        return word
    word = word.replace("SH", "Ş").replace("Sh", "Ş").replace("sh", "ş")
    return word.replace("CH", "Ç").replace("Ch", "Ç").replace("ch", "ç")


def old_latin_to_new(text: str) -> str:
    text = OLD_O.sub(lambda m: "Ö" if m.group(1) == "O" else "ö", text)
    text = OLD_G.sub(lambda m: "Ğ" if m.group(1) == "G" else "ğ", text)
    text = SEPARATED_SH.sub(lambda m: m.group(1) + "\x00", text)
    text = re.sub(r"[^\W\d_]+", lambda m: _word_to_new(m.group(0)), text)
    text = text.replace("\x00", "h")
    return OTHER_QUOTE.sub(TUTUQ, text)


def is_mostly_cyrillic(text: str) -> bool:
    cyr = len(re.findall(r"[А-Яа-яЁёЎўҚқҒғҲҳ]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return cyr > lat


HOMOGLYPHS = str.maketrans("аеорсухкмтАЕОРСУХКМТВН", "aeopcyxkmtAEOPCYXKMTBH")
MIXED_WORD = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[А-Яа-я])\w+\b")


def fix_homoglyphs(text: str) -> str:
    return MIXED_WORD.sub(lambda m: m.group(0).translate(HOMOGLYPHS), text)


def to_new_latin(text: str) -> str:
    text = html.unescape(text or "")
    if is_mostly_cyrillic(text):
        text = cyr_to_latin(text)
    return old_latin_to_new(fix_homoglyphs(text))
