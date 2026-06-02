"""Build a script.json + case_file.json for one Buncombe County conviction.

This bypasses the Gemini-driven federal scripter — we have structured
NCDPS data so we just template the narration and pre-resolve the images.

Output layout (under projects/<slug>/):
    case_file.json                       — defendant + court metadata
    script.json                          — scenes for produce_verdict_video
    visuals/source_images/mugshot.jpg    — fetched from NCDPS
    visuals/source_images/doc_*.png      — synthesized

Once these files exist, produce_verdict_video(project_dir) handles
narration, D3 render, and ffmpeg assembly with no further code changes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from production.local.mugshot_fetcher import fetch_ncdps_mugshot
from production.indicted.doc_synthesizer import synthesize_doc

ACCENT_BUNCOMBE = "#dc2626"   # match Verdict red


def slugify(text: str, max_len: int = 50) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s_-]", "", (text or "").lower())
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text or "case")[:max_len]


def _title_case(s: str) -> str:
    """NCDPS stores names in ALL CAPS. Display them as Title Case."""
    return " ".join(p.capitalize() for p in (s or "").split())


def _format_offense(code: str) -> str:
    """NCDPS offense codes look like 'COMMON LAW ROBBERY' or
    'TRAF METHAMPHETAMINE 28GM-200GM'. Convert to display form: each
    word title-cased, but tokens containing digits are kept verbatim
    (preserves measurements like 28GM, 1.5-10LBS)."""
    code = (code or "").strip()
    if not code:
        return "Unknown offense"
    parts = []
    for p in code.split():
        if any(ch.isdigit() for ch in p):
            parts.append(p)
        else:
            parts.append(p.capitalize())
    return " ".join(parts)


def _format_sentence(days_str: str) -> str:
    """CMMAXLEN is a zero-padded count of DAYS (verified empirically:
    115 days for drug paraphernalia, 1100 days for common-law robbery).
    Render as 'X years, Y months' or 'N days'. Returns a fallback string
    when the value is blank, zero, or NCDPS's `?????` placeholder."""
    days_str = (days_str or "").strip()
    if not days_str or "?" in days_str:
        return "an active prison sentence"
    try:
        d = int(days_str)
    except ValueError:
        return days_str
    if d <= 0:
        return "an active prison sentence"
    # Approximate: 365.25 days/year, then months from remainder.
    years = d // 365
    rem_days = d - years * 365
    months = rem_days // 30
    if years and months:
        return (f"{years} year{'s' if years != 1 else ''} and "
                f"{months} month{'s' if months != 1 else ''}")
    if years:
        return f"{years} year{'s' if years != 1 else ''}"
    if months:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{d} day{'s' if d != 1 else ''}"


def _format_date(yyyymmdd: str) -> str:
    """NCDPS dates come as YYYY-MM-DD or YYYYMMDD; render 'Month D, YYYY'."""
    s = (yyyymmdd or "").strip()
    if not s:
        return ""
    # Already YYYY-MM-DD?
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


def build_case_file(conv: dict) -> dict:
    """Produce a case_file.json equivalent for image_resolver compatibility."""
    full_name = " ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"), conv.get("last_name"),
    ] if p)
    return {
        "case_id": f"buncombe-{conv['opus_id']}",
        "headline": (f"{_title_case(full_name)} sentenced to "
                     f"{_format_sentence(conv.get('sentence_length_months'))} "
                     f"in Buncombe County"),
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
            "city": "Asheville",
            "state": "NC",
            "lat": 35.5951,
            "lon": -82.5515,
        }],
        "source": "ncdps_bulk",
    }


def build_scenes(conv: dict, project_dir: Path) -> list[dict]:
    """Build the scene list for produce_verdict_video. Images are
    pre-resolved (image_path filled in) so image_resolver becomes a no-op.
    """
    full_name = _title_case(" ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"), conv.get("last_name"),
    ] if p))
    offense_display = _format_offense(conv.get("most_serious_offense_code"))
    sentence_display = _format_sentence(conv.get("sentence_length_months"))
    sentence_date = _format_date(conv.get("sentence_effective_date"))
    admission_date = _format_date(conv.get("admission_date"))
    opus_id = conv["opus_id"]

    src_dir = project_dir / "visuals" / "source_images"
    src_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Mugshot ────────────────────────────────────────────────
    mug_path = src_dir / f"mugshot_{opus_id}.jpg"
    fetch_ncdps_mugshot(opus_id, mug_path)

    # ── 2. Court documents — synthesized with REAL NCDPS data ────
    # Doc 1: highlight the offense
    doc1 = src_dir / "doc_offense.png"
    synthesize_doc(
        quote=offense_display,
        case_no=f"NCDOC {opus_id}",
        court_name="Buncombe County Superior Court",
        doc_type="Judgment of Conviction",
        defendant_name=full_name,
        out_path=doc1,
    )

    # Doc 2: highlight the sentence length
    doc2 = src_dir / "doc_sentence.png"
    synthesize_doc(
        quote=(f"The defendant is hereby sentenced to an active term "
               f"of imprisonment of {sentence_display}, with sentence "
               f"effective {sentence_date or 'on the date of judgment'}."),
        case_no=f"NCDOC {opus_id}",
        court_name="Buncombe County Superior Court",
        doc_type="Sentencing Order",
        defendant_name=full_name,
        out_path=doc2,
    )

    # ── 3. Build the scene list ───────────────────────────────────
    scenes: list[dict] = []

    # s001 — opener
    scenes.append({
        "id": "s001",
        "narration": (f"A Buncombe County court has handed down a "
                      f"conviction. {full_name} was found guilty of "
                      f"{offense_display}."),
        "card": {
            "layout": "takeaway",
            "title": "Buncombe County, NC",
            "text": f"{full_name}\nCONVICTED",
            "accent": ACCENT_BUNCOMBE,
        },
    })

    # s002 — mugshot
    scenes.append({
        "id": "s002",
        "narration": (f"{full_name} was committed to the custody of the "
                      f"North Carolina Department of Adult Correction "
                      f"on {admission_date or sentence_date or 'the sentencing date'}."),
        "card": {
            "layout": "mugshot_card",
            "defendant_name": full_name,
            "image_path": str(mug_path) if mug_path.exists() else "",
            "subtitle": f"NCDOC #{opus_id}",
        },
    })

    # s003 — doc: offense
    scenes.append({
        "id": "s003",
        "narration": (f"The court found {full_name} guilty of "
                      f"{offense_display} — the most serious offense in "
                      f"the case."),
        "card": {
            "layout": "doc_screenshot",
            "image_path": str(doc1),
            "highlight": {"x": 0.08, "y": 0.55, "w": 0.84, "h": 0.13},
            "source_label": "Judgment of Conviction",
        },
    })

    # s004 — doc: sentence
    scenes.append({
        "id": "s004",
        "narration": (f"The court ordered an active sentence of "
                      f"{sentence_display}."),
        "card": {
            "layout": "doc_screenshot",
            "image_path": str(doc2),
            "highlight": {"x": 0.08, "y": 0.60, "w": 0.84, "h": 0.14},
            "source_label": "Sentencing Order",
        },
    })

    # s005 — closer
    scenes.append({
        "id": "s005",
        "narration": (f"{full_name} will serve {sentence_display} in North "
                      f"Carolina state custody. Source: NC Department of "
                      f"Adult Correction Offender Public Information."),
        "card": {
            "layout": "takeaway",
            "title": "Sentenced",
            "text": sentence_display,
            "accent": ACCENT_BUNCOMBE,
        },
    })

    return scenes


def write_project(conv: dict, projects_root: Path) -> Path:
    """Produce projects/buncombe-<opus_id>/{case_file,script}.json plus
    pre-resolved images. Returns the project directory."""
    slug = f"buncombe-{conv['opus_id']}"
    project_dir = projects_root / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    case = build_case_file(conv)
    (project_dir / "case_file.json").write_text(
        json.dumps(case, indent=2, ensure_ascii=False))

    full_name = _title_case(" ".join(p for p in [
        conv.get("first_name"), conv.get("middle_name"), conv.get("last_name"),
    ] if p))

    script = {
        "title": f"{full_name} — Buncombe County Conviction",
        "channel_handle": "@TheVerdict_USA",
        "accent": ACCENT_BUNCOMBE,
        "source": "ncdps",
        "source_opus_id": conv["opus_id"],
        "scenes": build_scenes(conv, project_dir),
    }
    (project_dir / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False))

    print(f"  [scripter] wrote {project_dir}", flush=True)
    return project_dir
