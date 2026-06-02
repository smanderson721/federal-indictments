#!/usr/bin/env python3
"""
production/indicted/image_resolver.py — walks a Verdict script.json and
fills in every ``card.image_path == "PLACEHOLDER"`` (or missing) by
dispatching to the appropriate fetcher / synthesizer:

    layout == "mugshot_card"   → mugshot_fetcher
    layout == "doc_screenshot" → doc_synthesizer
    layout == "street_view"    → streetview_fetcher

Reads the case_file.json (same directory) for the defendant name + role,
case number, and court name. Mutates `script` in place and rewrites
script.json. Safe to re-run — every fetcher caches on disk.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from production.indicted.mugshot_fetcher import fetch_mugshot
from production.indicted.streetview_fetcher import fetch_streetview
from production.indicted.doc_synthesizer import synthesize_doc


PLACEHOLDER_VALUES = ("", "PLACEHOLDER", "placeholder", None)


def _slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s_-]", "", (s or "").lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s or "x")[:max_len]


def _primary_defendant(case: dict) -> dict:
    defs = case.get("defendants") or []
    return defs[0] if defs else {"name": "John Doe", "role": ""}


def _location_query(card: dict, case: dict) -> str:
    """Build a Nominatim-friendly geocoder query.

    The scripter often produces verbose labels like
    "Federal Courthouse, Greenbelt, MD" or "Defendant's Residence, Bethesda, MD".
    Nominatim handles bare "City, State" much better, so we drop any
    leading descriptive phrase if there are >= 2 commas, keeping the
    last two segments (city, state).
    """
    label = (card.get("location_label") or "").strip()
    if label:
        parts = [p.strip() for p in label.split(",") if p.strip()]
        if len(parts) >= 2:
            # Keep the last two segments — "City, State"
            return ", ".join(parts[-2:])
        return label
    # Fall back to the first case location
    locs = case.get("locations") or []
    if locs:
        loc = locs[0]
        city = loc.get("city") or ""
        st = loc.get("state") or ""
        return ", ".join([p for p in (city, st) if p])
    return case.get("title") or "United States"


def _location_coords(card: dict, case: dict) -> tuple[float | None, float | None]:
    """If the matching case.locations entry has lat/lon, return them."""
    label = (card.get("location_label") or "").lower()
    for loc in case.get("locations") or []:
        city = (loc.get("city") or "").lower()
        if city and city in label and loc.get("lat") and loc.get("lon"):
            return float(loc["lat"]), float(loc["lon"])
    return None, None


def resolve_images(project_dir: str | Path,
                   *, force: bool = False) -> dict:
    """Walk script.json scenes and fill in image_path for every image-bearing
    layout. Returns a summary dict with counts per fetcher.
    """
    project_dir = Path(project_dir)
    script_path = project_dir / "script.json"
    case_path = project_dir / "case_file.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json not found in {project_dir}")
    if not case_path.exists():
        raise FileNotFoundError(f"case_file.json not found in {project_dir}")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)

    defendant = _primary_defendant(case)
    defendant_name = defendant.get("name") or "John Doe"
    defendant_role = defendant.get("role") or ""
    court = case.get("court") or {}
    case_no = court.get("case_no") or "—"
    court_name = (
        f"United States District Court for the {court['district']}"
        if court.get("district") else
        "United States District Court"
    )

    src_dir = project_dir / "visuals" / "source_images"
    src_dir.mkdir(parents=True, exist_ok=True)

    summary = {"mugshot": 0, "doc": 0, "streetview": 0,
               "skipped_present": 0, "failed": 0}

    for scene in script.get("scenes", []):
        card = scene.get("card") or {}
        layout = card.get("layout")
        existing = card.get("image_path")
        sid = scene.get("id") or "sNNN"

        if layout not in ("mugshot_card", "doc_screenshot", "street_view"):
            continue
        if existing not in PLACEHOLDER_VALUES and not force:
            summary["skipped_present"] += 1
            continue

        print(f"\n[{sid}] resolving image for layout={layout!r}", flush=True)

        if layout == "mugshot_card":
            out = src_dir / f"mugshot_{_slugify(card.get('defendant_name') or defendant_name)}.jpg"
            p = fetch_mugshot(
                card.get("defendant_name") or defendant_name,
                defendant_role,
                src_dir,
                force=force,
            )
            if p:
                card["image_path"] = str(p)
                summary["mugshot"] += 1
            else:
                summary["failed"] += 1

        elif layout == "doc_screenshot":
            out = src_dir / f"doc_{sid}.png"
            quote = (card.get("quote") or
                     card.get("image_caption") or "").strip()
            if not quote:
                quote = "The defendant is alleged to have violated federal law."
            p = synthesize_doc(
                quote,
                case_no=case_no,
                court_name=court_name,
                doc_type=(card.get("source_label") or "Indictment"),
                defendant_name=defendant_name,
                out_path=out,
                force=force,
            )
            if p:
                card["image_path"] = str(p)
                summary["doc"] += 1
            else:
                summary["failed"] += 1

        elif layout == "street_view":
            out = src_dir / f"streetview_{sid}.jpg"
            query = _location_query(card, case)
            lat, lon = _location_coords(card, case)
            p = fetch_streetview(
                query, out,
                lat=lat, lon=lon,
                force=force,
            )
            if p:
                card["image_path"] = str(p)
                summary["streetview"] += 1
            else:
                summary["failed"] += 1

    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  image resolver summary: {summary}")
    print(f"{'=' * 60}")
    return summary


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m production.indicted.image_resolver "
              "<project_dir> [--force]")
        sys.exit(1)
    force = "--force" in sys.argv[2:]
    resolve_images(sys.argv[1], force=force)
