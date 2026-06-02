"""Scrape NCDPS mugshots from the public view-offender pages."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"),
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def _try_get(url: str, timeout: float = 20.0) -> bytes | None:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=timeout) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "").lower()
            if not ctype.startswith("image/"):
                return None
            data = r.content
            # Reject GIF placeholders (silhouette.gif, spacer, etc).
            if (len(data) < 5000 and (data[:6] == b"GIF89a"
                                      or data[:6] == b"GIF87a")):
                return None
            # Reject NCDPS "No Photo Available" JPEG placeholder.
            if _is_no_photo_placeholder(data):
                return None
            return data
    except Exception as e:
        print(f"  [mugshot-nc] GET {url} failed: {e}", flush=True)
        return None


def _is_silhouette(src: str) -> bool:
    """Check if a URL or path looks like a silhouette/placeholder image."""
    s = src.lower()
    return (
        "silhouette" in s
        or "spacertbl" in s
        or s.endswith("/find.ico")
        or s.endswith(".gif")
    )


def _is_no_photo_placeholder(data: bytes) -> bool:
    """Detect NCDPS 'No Photo Available' JPEG placeholder.
    
    The placeholder is a 240x240 JPEG (~4.8 KB). Real mugshots are 
    typically larger (400x500+ pixels). We check JPEG dimensions via
    the SOF marker.
    """
    if len(data) < 4500 or len(data) > 6000:
        return False
    try:
        # Must be JPEG (starts with FFD8)
        if data[:2] != b"\xff\xd8":
            return False
        # Find Start-of-Frame (SOF0: FFC0, SOF1: FFC1, SOF2: FFC2, etc.)
        # SOF marker is at offset, followed by length (2 bytes), then:
        #   precision (1 byte), height (2 bytes), width (2 bytes)
        i = 2
        while i < len(data) - 8:
            if data[i:i+1] != b"\xff":
                i += 1
                continue
            marker = data[i+1:i+2]
            # SOF markers are C0-C3, C5-C7, C9-CB, CD-CF
            if marker[0:1] in (b"\xc0", b"\xc1", b"\xc2", b"\xc3",
                               b"\xc5", b"\xc6", b"\xc7",
                               b"\xc9", b"\xca", b"\xcb",
                               b"\xcd", b"\xce", b"\xcf"):
                # Extract height and width
                # Skip marker (2) + length (2) + precision (1)
                height = int.from_bytes(data[i+5:i+7], "big")
                width = int.from_bytes(data[i+7:i+9], "big")
                # Placeholder is always 240x240; real mugshots are 400+ pixels
                return height == 240 and width == 240
            i += 1
    except Exception:
        pass
    return False


def _scrape_view_page_for_image(opus_id: str) -> str | None:
    """Fetch the NCDPS view-offender page and extract the image URL."""
    try:
        url = (
            f"https://webapps.doc.state.nc.us/opi/viewoffender.do?"
            f"method=view&offenderID={opus_id}"
        )
        with httpx.Client(headers=HEADERS, follow_redirects=True,
                          timeout=20.0) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None
            html = r.text
    except Exception as e:
        print(f"  [mugshot-nc] viewoffender page fetch failed: {e}",
              flush=True)
        return None

    # Look for any <img src> that references the opus id or "photo".
    # We deliberately skip silhouette / spacer / icon images.
    candidates = re.findall(r'<img[^>]+src="([^"]+)"', html, re.IGNORECASE)
    for src in candidates:
        if _is_silhouette(src):
            continue
        looks_like_photo = (
            opus_id in src
            or "dopPicture" in src
            or "photo" in src.lower()
            or "/offphoto" in src.lower()
            or "mug" in src.lower()
        )
        if not looks_like_photo:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = "https://webapps.doc.state.nc.us" + src
        elif not src.startswith("http"):
            src = "https://webapps.doc.state.nc.us/opi/" + src.lstrip("./")
        return src
    return None


def fetch_ncdps_mugshot(opus_id: str, out_path: Path | str, *,
                        force: bool = False) -> Path | None:
    """Download a mugshot from NCDPS for the given opus_id.
    
    Returns the output path if successful, None if:
      - No image found on the view page
      - Only placeholder images available
      - Download failed
    
    Args:
        opus_id: NCDPS offender ID (7 digits)
        out_path: Where to save the mugshot
        force: Re-download even if file exists
    
    Returns:
        Path to the saved mugshot, or None
    """
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return out_path
    print(f"  [mugshot-nc] fetching photo for OPUS {opus_id}")
    url = _scrape_view_page_for_image(opus_id)
    if not url:
        print(f"  [mugshot-nc] no photo found for OPUS {opus_id}")
        return None
    print(f"  [mugshot-nc] scraped → {url}")
    data = _try_get(url)
    if not data:
        print(f"  [mugshot-nc] no photo found for OPUS {opus_id}")
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path.resolve()
