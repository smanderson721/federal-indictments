"""Build a script.json + case_file.json for one Buncombe County conviction.

Templated narration (no LLM). Pre-resolves all images upfront so the
production pipeline's image_resolver becomes a no-op.

Raises SkipCase to signal that the case should be marked skipped in the
DB and the harvester should advance to the next-newest unrendered record:

    SkipCase("no_photo", "NCDPS view page returned silhouette fallback")
    SkipCase("unknown_offense", "Offense code AWDXY not in offense_codes.json")
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from production.local.mugshot_fetcher import fetch_ncdps_mugshot
from production.local.vertical_doc import render_vertical_doc
from research.ncdps.offense_lookup import expand_code

ACCENT_BUNCOMBE = "#dc2626"


class SkipCase(Exception):
    """Raised when a conviction record can't be turned into a video.
    `reason` is one of: 'no_photo', 'unknown_offense'."""
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def slugify(text: str, max_len: int = 50) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s_-]", "", (text or "").lower())
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text or "case")[:max_len]


def _title_case(s: str) -> str:
    return " ".join(p.capitalize() for p in (s or "").split())


def _format_sentence(days_str: str) -> str:
    days_str = (days_str or "").strip()
    if not days_str or "?" in days_str:
        return "an active prison sentence"
    try:
        d = int(days_str)
    except ValueError:
        return days_str
    if d <= 0:
        return "an active prison sentence"
    years = d // 365
    rem_days = d - years * 365
    months = rem_days // 30
    # Very long sentences (60+ years) get reported as life-equivalent.
    if years >= 60:
        return "life in prison"
    if years and months:
        return (f"{years} year{'s' if years != 1 else ''} and "
                f"{months} month{'s' if months != 1 else ''}")
    if years:
        return f"{years} year{'s' if years != 1 else ''}"
    if months:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{d} day{'s' if d != 1 else ''}"


def _format_date(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if not m:
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if not m:
        return s
    import datetime
    try:
        d = datetime.date(int(m[1]), int(m[2]), int(m[3]))
        return d.strftime("%B %-d, %Y")
    except (ValueError, KeyError):
        return s


def build_case_file(conv: dict, offense_text: str,
                    sentence_text: str) -> dict:
    full_name = " ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"),
        conv.get("last_name"),
    ] if p)
    return {
        "case_id": f"buncombe-{conv['opus_id']}",
        "headline": (f"{_title_case(full_name)} sentenced to "
                     f"{sentence_text} for {offense_text} in Buncombe County"),
        "agency": "NC Department of Adult Correction",
        "filed_on": _format_date(conv.get("sentence_effective_date")),
        "defendants": [{
            "name": _title_case(full_name),
            "role": "Convicted defendant",
            "opus_id": conv["opus_id"],
        }],
        "court": {
            "name": "Buncombe County Superior Court",
            "district": "Buncombe County, NC",
            "case_no": f"NCDOC #{conv['opus_id']}",
        },
        "locations": [{
            "city": "Asheville", "state": "NC",
            "lat": 35.5951, "lon": -82.5515,
        }],
        "source": "ncdps_bulk",
    }


def build_scenes(conv: dict, project_dir: Path, *,
                 mugshot_path: Path,
                 offense_text: str,
                 sentence_text: str) -> list[dict]:
    full_name = _title_case(" ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"),
        conv.get("last_name"),
    ] if p))
    sentence_date = _format_date(conv.get("sentence_effective_date"))
    admission_date = _format_date(conv.get("admission_date"))
    opus_id = conv["opus_id"]

    src_dir = project_dir / "visuals" / "source_images"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Synthesized vertical docs (1080x1920, large readable text).
    doc_offense = src_dir / "doc_offense.png"
    render_vertical_doc(
        court_name="Buncombe County Superior Court",
        doc_type="Judgment of Conviction",
        case_no=f"NCDOC #{opus_id}",
        defendant_name=full_name,
        body=("The Court, having heard the evidence and the verdict "
              "of the jury, finds the above-named defendant guilty "
              "of the offense set forth herein."),
        highlight_excerpt=offense_text.upper(),
        out_path=doc_offense,
    )

    doc_sentence = src_dir / "doc_sentence.png"
    render_vertical_doc(
        court_name="Buncombe County Superior Court",
        doc_type="Sentencing Order",
        case_no=f"NCDOC #{opus_id}",
        defendant_name=full_name,
        body=("It is ORDERED that the defendant be sentenced to an "
              "active term of imprisonment in the custody of the "
              "North Carolina Department of Adult Correction."),
        highlight_excerpt=f"ACTIVE SENTENCE: {sentence_text.upper()}",
        out_path=doc_sentence,
    )

    return [
        {
            "id": "s001",
            "narration": (f"A Buncombe County court has handed down a "
                          f"conviction. {full_name} was found guilty of "
                          f"{offense_text}."),
            "card": {
                "layout": "takeaway",
                "title": "Buncombe County, NC",
                "text": f"{full_name}\nCONVICTED",
                "accent": ACCENT_BUNCOMBE,
            },
        },
        {
            "id": "s002",
            "narration": (
                f"{full_name} was committed to the custody of the North "
                f"Carolina Department of Adult Correction on "
                f"{admission_date or sentence_date or 'the sentencing date'}."),
            "card": {
                "layout": "mugshot_card",
                "defendant_name": full_name,
                "image_path": str(mugshot_path),
                "subtitle": f"NCDOC #{opus_id}",
            },
        },
        {
            "id": "s003",
            "narration": (f"The court found {full_name} guilty of "
                          f"{offense_text} — the most serious offense in "
                          f"the case."),
            "card": {
                "layout": "doc_screenshot",
                "image_path": str(doc_offense),
                "source_label": "Judgment of Conviction",
            },
        },
        {
            "id": "s004",
            "narration": (f"The court ordered an active sentence of "
                          f"{sentence_text}."),
            "card": {
                "layout": "doc_screenshot",
                "image_path": str(doc_sentence),
                "source_label": "Sentencing Order",
            },
        },
        {
            "id": "s005",
            "narration": (
                f"{full_name} will serve {sentence_text} in North Carolina "
                f"state custody. Source: NC Department of Adult Correction "
                f"Offender Public Information."),
            "card": {
                "layout": "takeaway",
                "title": "Sentenced",
                "text": sentence_text.title(),
                "accent": ACCENT_BUNCOMBE,
            },
        },
    ]


def write_project(conv: dict, projects_root: Path) -> Path:
    """Produce projects/buncombe-<opus_id>/{case_file,script}.json plus
    pre-resolved images. Returns the project directory.

    Raises SkipCase if the offense code is unknown or no mugshot exists.
    """
    opus_id = conv["opus_id"]
    slug = f"buncombe-{opus_id}"
    project_dir = projects_root / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    # Gate 1: offense code must be in our lookup.
    raw_code = conv.get("most_serious_offense_code") or ""
    offense_text = expand_code(raw_code)
    if not offense_text:
        raise SkipCase(
            "unknown_offense",
            f"NCDPS code {raw_code!r} not in offense_codes.json")

    # Gate 2: mugshot must exist (no silhouette fallback).
    src_dir = project_dir / "visuals" / "source_images"
    src_dir.mkdir(parents=True, exist_ok=True)
    mug_path = src_dir / f"mugshot_{opus_id}.jpg"
    fetched = fetch_ncdps_mugshot(opus_id, mug_path)
    if not fetched:
        raise SkipCase(
            "no_photo", f"NCDPS has no real photo for OPUS {opus_id}")

    sentence_text = _format_sentence(conv.get("sentence_length_months"))

    case = build_case_file(conv, offense_text, sentence_text)
    (project_dir / "case_file.json").write_text(
        json.dumps(case, indent=2, ensure_ascii=False))

    full_name = _title_case(" ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"),
        conv.get("last_name"),
    ] if p))

    script = {
        "title": f"{full_name} — Buncombe County Conviction",
        "channel_handle": "@TheVerdict_USA",
        "accent": ACCENT_BUNCOMBE,
        "source": "ncdps",
        "source_opus_id": opus_id,
        "scenes": build_scenes(conv, project_dir,
                               mugshot_path=mug_path,
                               offense_text=offense_text,
                               sentence_text=sentence_text),
    }
    (project_dir / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False))

    print(f"  [scripter] wrote {project_dir}", flush=True)
    return project_dir
