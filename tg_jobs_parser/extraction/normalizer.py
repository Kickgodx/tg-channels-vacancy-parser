"""Нормализация текста для матчинга правил (не для ``raw_text``)."""

from __future__ import annotations

import re
import unicodedata


def normalize_for_match(text: str) -> str:
    """
    Приводит текст к lowercase, нормализует Unicode, убирает лишние пробелы,
    унифицирует частые варианты написания удалёнки и валют.
    """
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("ё", "е")

    # Удалёнка
    for pat, rep in [
        (r"удалённ", "удаленн"),
        (r"удалёнк", "удаленк"),
        (r"remote[-\s]?first", "remote first"),
        (r"wfh", "work from home"),
    ]:
        t = re.sub(pat, rep, t)

    # Валюты (символ рубля часто без «границ слова»)
    t = t.replace("₽", " rub ")
    t = re.sub(r"\brub\b", " rub ", t)
    t = re.sub(r"\brur\b", " rub ", t)
    t = re.sub(r"\bруб\.?\b", " rub ", t)
    t = re.sub(r"\$", " usd ", t)
    t = re.sub(r"\b€\b", " eur ", t)
    t = re.sub(r"\beur\b", " eur ", t)
    t = re.sub(r"\busd\b", " usd ", t)

    # «к» / тыс
    t = re.sub(r"\b(\d+)\s*к\b", r"\1k", t)
    t = re.sub(r"\bтыс\.?\b", "k", t)
    t = re.sub(r"\bт\.?\s*руб", "k rub", t)

    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_display_text(text: str) -> str:
    """Человекочитаемая очистка без агрессивного lowercasing для ``cleaned_text``."""
    t = unicodedata.normalize("NFKC", text).strip()
    t = re.sub(r"\s+", " ", t)
    return t
