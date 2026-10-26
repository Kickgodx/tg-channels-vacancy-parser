"""Нормализованный ключ компании: «ООО «Ромашка»», «Ромашка», «РОМАШКА LLC» → «ромашка».

Ключ сводит варианты одного названия для профиля компании и топа работодателей; разные
компании с общим словом («Яндекс» и «Яндекс Крауд») остаются разными.
"""

from __future__ import annotations

import re
import unicodedata

# Организационно-правовые формы — отдельным словом в любом месте названия.
_LEGAL_FORMS = (
    "ооо",
    "оао",
    "зао",
    "пао",
    "нао",
    "ао",
    "ип",
    "гк",
    "llc",
    "ltd",
    "inc",
    "gmbh",
    "llp",
    "plc",
    "corp",
)
_LEGAL_RE = re.compile(r"(?<![0-9a-zа-я])(?:" + "|".join(_LEGAL_FORMS) + r")(?![0-9a-zа-я])")
_NON_WORD_RE = re.compile(r"[^0-9a-zа-я]+")


def company_key(name: str | None) -> str | None:
    """Ключ для группировки; ``None`` — названия нет или в нём только форма собственности."""
    if not name:
        return None
    t = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    t = _NON_WORD_RE.sub(" ", t)
    t = _LEGAL_RE.sub(" ", t)
    t = " ".join(t.split())
    return t[:256] or None
