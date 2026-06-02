"""Download NCDPS bulk offender tables with Last-Modified-based caching.

The files live at:
    https://opus.doc.state.nc.us/offenders/<TABLE>.zip

We cache each zip in <repo>/research_output/ncdps/cache/. A subsequent
download uses If-Modified-Since to avoid re-downloading unchanged files.
"""

from __future__ import annotations

import email.utils
import os
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "research_output" / "ncdps" / "cache"
BASE_URL = "https://opus.doc.state.nc.us/offenders"

UA = ("VideoEssaysVerdict/1.0 (https://github.com/smanderson721/"
      "federal-indictments; harvester)")
HEADERS = {"User-Agent": UA}

# NCDPS table identifiers we care about.
TABLE_OFFENDER_PROFILE  = "OFNT3AA1"   # 35 MB  — basic identity per offender
TABLE_INMATE_PROFILE    = "INMT4AA1"   # 40 MB  — inmate detail
TABLE_COURT_COMMITMENT  = "OFNT3BB1"   # 105 MB — county + sentence per commit
TABLE_SENTENCE_COMPONENT = "OFNT3CE1"  # 235 MB — offense codes per sentence


def cached_path(table: str) -> Path:
    return CACHE_DIR / f"{table}.zip"


def download_if_modified(table: str, *, force: bool = False,
                         timeout: float = 600.0) -> Path:
    """Download `<TABLE>.zip` into the cache, using If-Modified-Since to
    skip the download if the cached copy is current. Returns the cached
    file path."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = cached_path(table)
    url = f"{BASE_URL}/{table}.zip"

    headers = dict(HEADERS)
    if dest.exists() and not force:
        mtime = email.utils.formatdate(dest.stat().st_mtime, usegmt=True)
        headers["If-Modified-Since"] = mtime

    print(f"  [ncdps] {table}: GET {url}", flush=True)
    t0 = time.time()
    with httpx.Client(headers=headers, follow_redirects=True,
                      timeout=timeout) as c:
        with c.stream("GET", url) as r:
            if r.status_code == 304:
                print(f"  [ncdps] {table}: not modified (cached, "
                      f"{dest.stat().st_size // 1024 // 1024} MB)",
                      flush=True)
                return dest
            r.raise_for_status()
            total = 0
            tmp = dest.with_suffix(".zip.tmp")
            with open(tmp, "wb") as out:
                for chunk in r.iter_bytes(chunk_size=1 << 20):
                    out.write(chunk)
                    total += len(chunk)
            tmp.replace(dest)
            # Stamp the file mtime to match the server's Last-Modified so
            # the next run's If-Modified-Since works correctly.
            lm = r.headers.get("last-modified")
            if lm:
                try:
                    ts = email.utils.parsedate_to_datetime(lm).timestamp()
                    os.utime(dest, (ts, ts))
                except Exception:
                    pass
    print(f"  [ncdps] {table}: downloaded {total // 1024 // 1024} MB "
          f"in {time.time() - t0:.1f}s", flush=True)
    return dest
