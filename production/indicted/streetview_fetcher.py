#!/usr/bin/env python3
"""
production/indicted/streetview_fetcher.py — fetch a street-level / aerial
image of a location for the `street_view` layout.

Strategy:
  1. If GOOGLE_MAPS_API_KEY is set in env, use Google Static Street View API
     (best quality, real street-level photo).
  2. Otherwise, geocode via Nominatim (OpenStreetMap) and pull a high-zoom
     aerial composite from the OSM static map service at
     `https://staticmap.openstreetmap.de/staticmap.php`. This is a tiled
     map view (not a true street-level photo) but reads as a credible
     "courthouse / scene location" overhead shot, especially with the
     template's gradient overlays and pin label.

Output: writes the image to
    projects/<slug>/visuals/source_images/streetview_<sNNN>.jpg
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import io
import math

import httpx
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


UA = "VideoEssaysVerdict/1.0 (verdict-streetview-fetcher; localhost)"
HEADERS = {"User-Agent": UA}

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
GOOGLE_STATIC_STREETVIEW = "https://maps.googleapis.com/maps/api/streetview"


def _slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s_-]", "", (s or "").lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s or "location")[:max_len]


# ── Geocoding (Nominatim) ───────────────────────────────────────────
def _geocode(query: str) -> Optional[Tuple[float, float]]:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=20.0) as c:
            r = c.get(NOMINATIM, params={
                "q": query,
                "format": "json",
                "limit": 1,
            })
            r.raise_for_status()
            data = r.json()
            if not data:
                return None
            return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception as e:
        print(f"  [streetview] geocode failed for {query!r}: {e}", flush=True)
        return None


# ── Source 1: Google Static Street View ─────────────────────────────
def _google_streetview(lat: float, lon: float, *,
                       heading: Optional[float] = None) -> Optional[bytes]:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return None
    params = {
        "size": "1080x1080",
        "location": f"{lat},{lon}",
        "fov": "80",
        "pitch": "10",
        "key": api_key,
    }
    if heading is not None:
        params["heading"] = str(int(heading))
    try:
        with httpx.Client(timeout=25.0) as c:
            r = c.get(GOOGLE_STATIC_STREETVIEW, params=params)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                return None
            return r.content
    except Exception as e:
        print(f"  [streetview] google fetch failed: {e}", flush=True)
        return None


# ── Source 2: OSM tiles stitched (fallback) ─────────────────────────
def _deg2tile(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    """Slippy-map tile coordinates (fractional)."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xt = (lon + 180.0) / 360.0 * n
    yt = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad))
          / math.pi) / 2.0 * n
    return xt, yt


def _fetch_tile(client: httpx.Client, z: int, x: int, y: int) -> Optional[Image.Image]:
    url = OSM_TILE_URL.format(z=z, x=x, y=y)
    try:
        r = client.get(url)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"  [streetview] tile {z}/{x}/{y} failed: {e}", flush=True)
        return None


def _osm_static(lat: float, lon: float,
                *, zoom: int = 17, size: int = 1080) -> Optional[bytes]:
    """Stitch OSM tiles centered on (lat,lon) into a `size`x`size` PNG."""
    xt, yt = _deg2tile(lat, lon, zoom)
    px_cx = xt * 256.0
    px_cy = yt * 256.0
    px_left = px_cx - size / 2
    px_top  = px_cy - size / 2
    tx_min = int(math.floor(px_left / 256))
    ty_min = int(math.floor(px_top / 256))
    tx_max = int(math.floor((px_left + size) / 256))
    ty_max = int(math.floor((px_top  + size) / 256))

    canvas_w = (tx_max - tx_min + 1) * 256
    canvas_h = (ty_max - ty_min + 1) * 256
    canvas = Image.new("RGB", (canvas_w, canvas_h), (240, 240, 240))

    with httpx.Client(headers=HEADERS, follow_redirects=True,
                      timeout=20.0) as c:
        for tx in range(tx_min, tx_max + 1):
            for ty in range(ty_min, ty_max + 1):
                tile = _fetch_tile(c, zoom, tx, ty)
                if tile is None:
                    continue
                canvas.paste(tile,
                             ((tx - tx_min) * 256, (ty - ty_min) * 256))

    # Crop to the requested window
    crop_x = int(px_left - tx_min * 256)
    crop_y = int(px_top  - ty_min * 256)
    img = canvas.crop((crop_x, crop_y,
                       crop_x + size, crop_y + size))

    # Draw a red pin at the center
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    # pin drop shadow
    draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22],
                 fill=(0, 0, 0, 80))
    # pin body
    draw.ellipse([cx - 20, cy - 20, cx + 20, cy + 20],
                 fill=(220, 38, 38), outline=(255, 255, 255), width=4)
    # pin inner dot
    draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ── Public API ──────────────────────────────────────────────────────
def fetch_streetview(query: str,
                     out_path: Path,
                     *,
                     lat: Optional[float] = None,
                     lon: Optional[float] = None,
                     force: bool = False) -> Optional[Path]:
    """Fetch a location image for `query` (e.g. "Federal Courthouse,
    Greenbelt, MD"). If lat/lon are supplied, skip geocoding.

    Returns the absolute path of the written image, or None on failure.
    Cached on disk; pass force=True to refetch.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path.resolve()

    if lat is None or lon is None:
        print(f"  [streetview] geocoding {query!r}...", flush=True)
        coords = _geocode(query)
        time.sleep(1.0)   # Nominatim politeness
        if not coords:
            print(f"  [streetview] no coordinates for {query!r}", flush=True)
            return None
        lat, lon = coords

    # 1. Google Street View (if key present)
    blob = _google_streetview(lat, lon)
    source = "google"

    # 2. OSM static map fallback
    if not blob:
        blob = _osm_static(lat, lon)
        source = "osm"

    if not blob:
        print(f"  [streetview] all sources failed for {query!r}", flush=True)
        return None

    out_path.write_bytes(blob)
    print(f"  [streetview] {source} → {out_path.name} "
          f"({len(blob) // 1024} KB)", flush=True)
    return out_path.resolve()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m production.indicted.streetview_fetcher "
              "'Location query' [out_path]")
        sys.exit(1)
    q = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 \
        else Path("/tmp/streetview_test.jpg")
    p = fetch_streetview(q, out, force=True)
    print(f"\nResult: {p}")
