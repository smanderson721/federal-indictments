#!/usr/bin/env python3
"""
production/indicted/mugshot_fetcher.py — Fetch a defendant photo.

Strategy (each step short-circuits on success):
  1. Wikipedia REST API summary for the defendant's name → `thumbnail.source`.
     Works well for public figures (politicians, executives, celebrities).
  2. Gemini search-grounded prompt asking for a single freely-available
     photo URL (Wikimedia, government press releases, DOJ, FBI). The model
     returns one URL and we download it.

Output: writes the image to
    projects/<slug>/visuals/source_images/mugshot_<defendant_slug>.jpg
and returns the absolute path. Returns None on failure.

The vertical_indicted.html template falls back to its amber-striped
placeholder when image_path is missing or fails to load.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config

WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

UA = "VideoEssaysVerdict/1.0 (https://localhost; pipeline image fetcher)"
HEADERS = {"User-Agent": UA}

ACCEPTED_IMAGE_MIME = ("image/jpeg", "image/png", "image/webp")


def _slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s_-]", "", (text or "").lower())
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text or "defendant")[:max_len]


def _download(url: str, dest: Path, timeout: float = 25.0) -> bool:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=timeout) as c:
            r = c.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower().split(";")[0]
            if ctype and not any(ctype.startswith(m) for m in ACCEPTED_IMAGE_MIME):
                print(f"  [mugshot] skip non-image content-type: {ctype}",
                      flush=True)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"  [mugshot] download failed for {url}: {e}", flush=True)
        return False


# ──────────────────────────────────────────────────────────────────
# Source 1: Wikipedia
# ──────────────────────────────────────────────────────────────────

def _wikipedia_image(name: str) -> Optional[str]:
    """Return the lead-image URL from the Wikipedia page named `name`,
    or None if the page or thumbnail is missing."""
    title = name.replace(" ", "_")
    url = WIKI_SUMMARY_URL.format(title=httpx.URL(title).raw_path.decode())
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=15.0) as c:
            r = c.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"  [mugshot] wikipedia lookup failed: {e}", flush=True)
        return None
    # Prefer originalimage (full-res), fall back to thumbnail.
    orig = (data.get("originalimage") or {}).get("source")
    if orig:
        return orig
    thumb = (data.get("thumbnail") or {}).get("source")
    return thumb


# ──────────────────────────────────────────────────────────────────
# Source 2: Gemini search grounding
# ──────────────────────────────────────────────────────────────────

_GEMINI_PROMPT = """You are helping locate a freely-available photograph for
a news segment.

Subject: {name}
Role / context: {role}

Find ONE publicly available, news-quality photograph of this person.
Strongly prefer (in order):
  1. Wikimedia Commons (commons.wikimedia.org)
  2. Government / agency press photos (.gov sites, DOJ, FBI, congressional)
  3. Official portrait pages

Return STRICTLY valid JSON, nothing else:
{{"image_url": "https://...", "source": "Wikimedia | gov | other"}}

The URL must point directly to a .jpg, .jpeg, .png, or .webp file (not an
HTML page). If you cannot find a direct image URL, return
{{"image_url": null, "source": null}}."""


def _gemini_image_url(name: str, role: str) -> Optional[str]:
    if not config.GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = _GEMINI_PROMPT.format(name=name, role=role or "(unspecified)")
    try:
        resp = client.models.generate_content(
            model=config.MODEL_SCORING,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )
        text = (resp.text or "").strip()
    except Exception as e:
        print(f"  [mugshot] gemini call failed: {e}", flush=True)
        return None
    # Strip markdown fences if any
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    url = data.get("image_url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def fetch_mugshot(defendant_name: str,
                  defendant_role: str,
                  out_dir: Path,
                  *, force: bool = False) -> Optional[Path]:
    """Locate and download a photograph of `defendant_name`.

    Returns the absolute path of the downloaded image, or None on failure.
    Cached on disk — re-runs skip the download unless `force=True`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"mugshot_{_slugify(defendant_name)}.jpg"
    if out_path.exists() and not force:
        return out_path.resolve()

    print(f"  [mugshot] fetching photo of {defendant_name!r}...", flush=True)

    # 1. Wikipedia
    url = _wikipedia_image(defendant_name)
    if url:
        print(f"  [mugshot] wikipedia → {url}", flush=True)
        if _download(url, out_path):
            return out_path.resolve()

    # 2. Gemini grounded
    url = _gemini_image_url(defendant_name, defendant_role or "")
    if url:
        print(f"  [mugshot] gemini → {url}", flush=True)
        if _download(url, out_path):
            return out_path.resolve()

    print(f"  [mugshot] FAILED to locate photo for {defendant_name}",
          flush=True)
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python -m production.indicted.mugshot_fetcher "
              "'Defendant Name' 'Role' [out_dir]")
        sys.exit(1)
    name = sys.argv[1]
    role = sys.argv[2]
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/mugshots")
    path = fetch_mugshot(name, role, out)
    print(f"\nResult: {path}")
