"""Fetch the mugshot for an NCDPS-convicted offender.

Strategy:
    1. Hit the offender view page for the offender's OPUS ID.
       https://webapps.doc.state.nc.us/opi/viewoffender.do
            ?method=view&offenderID=<opus_id>
    2. Scrape the page for the `<img src="...">` of the mugshot.
       NCDPS serves photos from `offphoto/<opus_id>.jpg`.
    3. Download the image to projects/<slug>/visuals/source_images/.

Returns the absolute path of the downloaded image, or None if no photo
is available for this offender (some records lack a photo).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

UA = ("VideoEssaysVerdict/1.0 (https://github.com/smanderson721/"
      "federal-indictments; mugshot fetcher)")
HEADERS = {"User-Agent": UA}

VIEW_URL = ("https://webapps.doc.state.nc.us/opi/viewoffender.do"
            "?method=view&offenderID={opus_id}")
# Direct image URLs NCDPS commonly serves under
_IMAGE_CANDIDATES = [
    "https://webapps.doc.state.nc.us/opi/offphoto/{opus_id}.jpg",
    "https://webapps.doc.state.nc.us/opi/offphoto/{opus_id}_HEAD.jpg",
    "https://opus.doc.state.nc.us/photos/{opus_id}.jpg",
]


def _try_get(url: str, timeout: float = 20.0) -> bytes | None:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=timeout) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "").lower()
            # Accept image responses only
            if not ctype.startswith("image/"):
                return None
            return r.content
    except Exception as e:
        print(f"  [mugshot-nc] GET {url} failed: {e}", flush=True)
        return None


def _scrape_view_page_for_image(opus_id: str) -> Optional[str]:
    url = VIEW_URL.format(opus_id=opus_id)
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=20.0) as c:
            r = c.get(url)
            r.raise_for_status()
            html = r.text
    except Exception as e:
        print(f"  [mugshot-nc] view page fetch failed: {e}", flush=True)
        return None

    # Look for any <img src> referencing the opus id or "photo"
    candidates = re.findall(r'<img[^>]+src="([^"]+)"', html, re.IGNORECASE)
    for src in candidates:
        if (opus_id in src
                or "photo" in src.lower()
                or "offender" in src.lower()
                or "mug" in src.lower()):
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://webapps.doc.state.nc.us" + src
            elif not src.startswith("http"):
                src = "https://webapps.doc.state.nc.us/opi/" + src.lstrip("./")
            return src
    return None


def fetch_ncdps_mugshot(opus_id: str, out_path: Path,
                        *, force: bool = False) -> Optional[Path]:
    """Locate and download the NCDPS mugshot for `opus_id`. Returns the
    absolute output path, or None on failure. Cached on disk."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path.resolve()

    print(f"  [mugshot-nc] fetching photo for OPUS {opus_id}", flush=True)

    # 1. Try the canonical photo URLs directly.
    for tmpl in _IMAGE_CANDIDATES:
        url = tmpl.format(opus_id=opus_id)
        data = _try_get(url)
        if data:
            out_path.write_bytes(data)
            print(f"  [mugshot-nc] direct → {url}", flush=True)
            return out_path.resolve()

    # 2. Fall back to scraping the view page.
    img_url = _scrape_view_page_for_image(opus_id)
    if img_url:
        data = _try_get(img_url)
        if data:
            out_path.write_bytes(data)
            print(f"  [mugshot-nc] scraped → {img_url}", flush=True)
            return out_path.resolve()

    print(f"  [mugshot-nc] no photo found for OPUS {opus_id}", flush=True)
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m production.local.mugshot_fetcher <OPUS_ID>")
        sys.exit(1)
    opus = sys.argv[1]
    out = Path(f"/tmp/ncdps_mug_{opus}.jpg")
    p = fetch_ncdps_mugshot(opus, out, force=True)
    print(f"\nResult: {p}")
