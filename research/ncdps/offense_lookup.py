"""NCDPS abbreviated offense code -> human-readable name lookup.

The bulk-download CMPRIOFF field stores cryptic 7-30 character codes like
'AWDWWITK'. We maintain a hand-curated map in offense_codes.json. Codes
not in the map cause the case to be skipped (logged + DB status flipped),
so the operator can periodically extend the map.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("offense_codes.json")
_CACHE: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _CACHE
    if _CACHE is None:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _CACHE = {k.upper().strip(): v for k, v in raw.items()
                  if not k.startswith("_")}
    return _CACHE


def normalize_code(code: str) -> str:
    """Uppercase, collapse interior whitespace, strip."""
    return re.sub(r"\s+", " ", (code or "").upper()).strip()


def expand_code(code: str) -> str | None:
    """Return the English form of a NCDPS offense code, or None if not
    in the map."""
    code = normalize_code(code)
    if not code:
        return None
    table = _load()
    if code in table:
        return table[code]
    # Try a few common variants
    for variant in (code.replace("&", "AND"), code.replace(" AND ", "&")):
        if variant in table:
            return table[variant]
    return None


def known_codes() -> list[str]:
    return sorted(_load().keys())
