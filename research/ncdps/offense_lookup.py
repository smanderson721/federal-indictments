"""NCDPS offense-code -> readable English.

NCDPS stores the most-serious offense in two formats:
  * Short cryptic codes like 'AWDWWITK' (8 chars, no spaces)
    -> covered by the hand-curated map in offense_codes.json
  * Already-expanded 30-char abbreviated phrases like
    'OBT PROP BY FALSE PR/CHTS/SER' or 'LARCENY AFTER B & E'
    -> handled by an abbreviation cleaner so we don't have to enumerate
       every variant in the JSON map

Resolution order:
  1. Exact hit in offense_codes.json
  2. Heuristic cleanup of an already-phrasal value
  3. None -> caller should treat as 'unknown_offense' and skip the case
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


# Token-level abbreviation expansions used by the phrasal cleaner.
# Matched case-insensitively after splitting on whitespace.
_WORD_EXPANSIONS = {
    "OBT": "obtaining",
    "PROP": "property",
    "PR": "pretenses",
    "CHTS": "cheats",
    "SER": "services",
    "POSS": "possession",
    "PWISD": "with intent to sell or deliver",
    "PWIMSD": "with intent to manufacture, sell, or deliver",
    "MFG": "manufacture",
    "MFR": "manufacture",
    "SCH": "schedule",
    "MARIJ": "marijuana",
    "METH": "methamphetamine",
    "COCA": "cocaine",
    "COC": "cocaine",
    "FENT": "fentanyl",
    "TRAF": "trafficking",
    "TRAFF": "trafficking",
    "DEL": "delivery",
    "DELIV": "delivery",
    "B&E": "breaking and entering",
    "W/": "with",
    "WO/": "without",
    "AWDW": "assault with a deadly weapon",
    "AWDWWITK": "assault with a deadly weapon with intent to kill",
    "AWDWWITKISI": ("assault with a deadly weapon with intent to "
                    "kill inflicting serious injury"),
    "AWDWISI": ("assault with a deadly weapon inflicting "
                "serious injury"),
    "ASLT": "assault",
    "STRANG": "strangulation",
    "FAIL": "failure",
    "NOTFY": "to notify",
    "NTFY": "to notify",
    "CHG": "change of",
    "ADDR": "address",
    "SEXOFF": "as a sex offender",
    "IDENT": "identity",
    "FRAUD": "fraud",
    "THEFT": "theft",
    "LARC": "larceny",
    "LARCENY": "larceny",
    "ROB": "robbery",
    "BUR": "burglary",
    "BURG": "burglary",
    "MURDER": "murder",
    "VOL": "voluntary",
    "INVOL": "involuntary",
    "MANSL": "manslaughter",
    "MANSLAU": "manslaughter",
    "DEG": "degree",
    "1ST": "first-degree",
    "2ND": "second-degree",
    "3RD": "third-degree",
    "DWI": "DWI",
    "LEVEL": "Level",
    "FIREARM": "firearm",
    "FELON": "felon",
    "FELONY": "felony",
    "HABIT": "habitual",
    "HAB": "habitual",
    "MV": "motor vehicle",
    "M/V": "motor vehicle",
    "VEH": "vehicle",
    "STOLEN": "stolen",
    "LEO": "law enforcement officer",
    "POL": "police officer",
    "RESIST": "resisting",
    "OFFICER": "officer",
    "BATT": "battery",
    "INJ": "injury",
    "PROB": "probation",
    "VIOL": "violation",
    "CONSPIRE": "conspiracy",
    "ATT": "attempted",
    "ATTM": "attempted",
    "AGG": "aggravated",
    "SEX": "sex",
    "SEXUAL": "sexual",
    "BATTERY": "battery",
    "MINOR": "minor",
    "CHILD": "child",
    "PORN": "pornography",
    "CONT": "controlled",
    "SUBST": "substance",
    "INDEC": "indecent",
    "LIB": "liberties",
    "EXPL": "exploitation",
    "ABUS": "abuse",
    "MISC": "miscellaneous",
    "OTHER": "other",
    "MISD": "misdemeanor",
    "OBSTRUCT": "obstruction",
    "JUST": "of justice",
    "AFTER": "after",
    "FROM": "from",
}

_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from",
                "in", "of", "on", "or", "the", "to", "with"}


def _cleanup_phrase(raw: str) -> str:
    """Best-effort English from an already-phrasal NCDPS offense string."""
    text = raw.strip()
    if not text:
        return ""

    # Normalise NCDPS's "B & E" and "B&E" to a canonical token,
    # then collapse slashes to spaces (NCDPS uses them as separators).
    text = re.sub(r"\bB\s*&\s*E\b", "B&E", text, flags=re.IGNORECASE)
    text = text.replace("/", " ")

    # Tokenise on whitespace.
    rough = re.split(r"(\s+)", text)
    out_tokens: list[str] = []
    for p in rough:
        if not p:
            continue
        if re.fullmatch(r"\s+", p):
            out_tokens.append(" ")
            continue
        key = p.upper()
        if key in _WORD_EXPANSIONS:
            out_tokens.append(_WORD_EXPANSIONS[key])
        else:
            out_tokens.append(p.lower())

    joined = re.sub(r"\s+", " ", "".join(out_tokens)).strip()
    # Collapse doubled connector words from expansions colliding with
    # already-present prepositions (e.g. "by by a felon").
    joined = re.sub(r"\b(of|by|with|the|a|an)\s+\1\b", r"\1",
                    joined, flags=re.IGNORECASE)

    # Title-case (preserving small words mid-phrase and DWI/PWISD).
    tokens = joined.split(" ")
    cased: list[str] = []
    for i, t in enumerate(tokens):
        if not t:
            continue
        upper = t.upper()
        if upper in ("DWI", "PWISD"):
            cased.append(upper)
        elif i > 0 and t.lower() in _SMALL_WORDS:
            cased.append(t.lower())
        elif t in ("/",):
            cased.append(t)
        else:
            cased.append("-".join(w.capitalize() for w in t.split("-")))
    return " ".join(cased)


def expand_code(code: str) -> str | None:
    """Return the English form of a NCDPS offense, or None if unrecognisable.

    Resolution order: exact map -> simple variants -> phrasal cleaner."""
    code = normalize_code(code)
    if not code:
        return None
    table = _load()
    if code in table:
        return table[code]
    for variant in (code.replace("&", "AND"), code.replace(" AND ", "&"),
                    code.replace("/", " "), code.replace("&", " AND ")):
        v = normalize_code(variant)
        if v in table:
            return table[v]

    # If the value is phrasal (contains whitespace or '/') or is
    # longer than a typical cryptic code, run it through the cleaner.
    if re.search(r"[\s/]", code) or len(code) >= 12:
        cleaned = _cleanup_phrase(code)
        # Accept whenever the cleaner produced something different from
        # the raw uppercase input (i.e. it actually normalised the
        # phrase). This still rejects single cryptic acronyms like
        # 'AWDWWITK' because they have no whitespace and the JSON map
        # path handles them.
        if cleaned and cleaned.upper().replace(" ", "") != \
                code.replace(" ", "").replace("/", ""):
            return cleaned
        if cleaned and cleaned != code:
            return cleaned
    return None


def known_codes() -> list[str]:
    return sorted(_load().keys())
