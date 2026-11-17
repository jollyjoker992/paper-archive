"""The terminology catalog: analytes, units, ECG and imaging codes (paperarchive/catalog/*.json)."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"


def fold(text: str) -> str:
    """Lower case, no diacritics, single spaces: for comparing printed names."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip().casefold()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((CATALOG_DIR / name).read_text(encoding="utf-8"))


def analytes() -> dict[str, dict]:
    return {a["key"]: a for a in _load("analytes.json")["analytes"]}


def codes() -> dict:
    return _load("codes.json")


def studies() -> dict[str, dict]:
    return {s["key"]: s for s in codes()["studies"]}


def catalog_version() -> str:
    return f'{_load("analytes.json")["version"]}+{codes()["version"]}'


_SUPERSCRIPT = str.maketrans({"µ": "u", "μ": "u", "²": "2", "³": "3", "⁹": "9", "⁶": "6", "¹": "1", "×": "x"})


def ucum(printed: str) -> str | None:
    """UCUM code of a printed unit, or None when the unit is not in units.json."""
    key = re.sub(r"\s+", "", printed.translate(_SUPERSCRIPT))
    return _load("units.json")["units"].get(key)


def analyte_code(key: str, unit: str | None) -> dict | None:
    """The catalog code of an analyte for a UCUM unit (or the only code of a qualitative analyte)."""
    entry = analytes().get(key)
    if not entry:
        return None
    if not entry["codes"]:
        return None
    if entry.get("qualitative"):
        return entry["codes"][0]
    return next((c for c in entry["codes"] if unit in c["units"]), None)


def find(text: str, kind: str = "analyte") -> list[dict]:
    """Catalog entries whose key or printed names match the text (exact first, then partial)."""
    needle = fold(text)
    entries = list(analytes().values()) if kind == "analyte" else codes()["studies"]
    exact, partial = [], []
    for entry in entries:
        names = [fold(n) for n in [entry["key"], *entry["names"]]]
        if needle in names:
            exact.append(entry)
        elif any(needle in n or n in needle for n in names if len(n) > 2):
            partial.append(entry)
    return exact + partial
