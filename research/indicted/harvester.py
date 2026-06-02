"""
Federal crime-feed harvester.

Polls every enabled feed in the `sources` table, parses entries via
`feedparser`, and inserts new events into the `events` table with
status='new'. Existing events are skipped via the UNIQUE constraint on
`guid` — re-running the harvester is safe and cheap.

Designed to run as a systemd timer on cassidy every 15 minutes. Single
pass takes a few seconds when nothing's changed (HTTP 304 / cached
parses) and tens of seconds on a full refresh.

Usage:
    python -m research.indicted.harvester                 # one pass
    python -m research.indicted.harvester --seed          # ensure default feeds present
    python -m research.indicted.harvester --stats         # print DB stats
"""

from __future__ import annotations

import argparse
import hashlib
import time
from calendar import timegm

from . import db
from .feeds import DEFAULT_FEEDS


def seed_default_sources(conn) -> int:
    """Insert any default feeds not already present. Returns count added."""
    added = 0
    for feed in DEFAULT_FEEDS:
        before = conn.execute("SELECT id FROM sources WHERE name=?",
                              (feed["name"],)).fetchone()
        db.upsert_source(
            conn,
            name=feed["name"],
            url=feed["url"],
            kind=feed["kind"],
            jurisdiction=feed.get("jurisdiction"),
            enabled=feed.get("enabled", True),
        )
        if not before:
            added += 1
    return added


def _entry_guid(entry) -> str:
    """Pick the most stable identifier from a feedparser entry.

    Prefer `entry.id` / `entry.guid` (RSS GUID, atom <id>). Fall back
    to `entry.link`. Last resort: sha1 of title+published.
    """
    for attr in ("id", "guid", "link"):
        v = getattr(entry, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    title = (getattr(entry, "title", "") or "").strip()
    pub = (getattr(entry, "published", "") or "").strip()
    h = hashlib.sha1((title + "|" + pub).encode("utf-8")).hexdigest()
    return f"sha1:{h}"


def _entry_published(entry) -> int | None:
    """Return epoch seconds for the entry's published date, or None."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t is not None:
            try:
                return int(timegm(t))
            except Exception:
                pass
    return None


def _entry_summary(entry) -> str:
    """Strip HTML from the entry summary, cap length to keep DB lean."""
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    if not raw:
        return ""
    # Minimal HTML strip — we don't need a full parser here. The full raw
    # JSON is preserved in the `raw` column for downstream processing.
    import re
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def harvest_one_source(conn, source_row) -> dict:
    """Fetch + insert events for a single source. Returns counters."""
    import feedparser  # lazy import so --stats works without it installed

    src_id = source_row["id"]
    url = source_row["url"]
    name = source_row["name"]

    run_id = db.start_run(conn, src_id)
    new_count = 0
    seen_count = 0
    err: str | None = None
    try:
        parsed = feedparser.parse(
            url,
            agent="IndictedHarvester/1.0 (+https://indicted.goods-live.com)",
            request_headers={"Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.5"},
        )
        if parsed.bozo and not parsed.entries:
            err = f"feedparser bozo: {parsed.bozo_exception!r}"
        else:
            for entry in parsed.entries:
                seen_count += 1
                guid = _entry_guid(entry)
                title = (getattr(entry, "title", "") or "").strip()
                link = (getattr(entry, "link", "") or "").strip()
                summary = _entry_summary(entry)
                published = _entry_published(entry)
                raw = {k: getattr(entry, k, None) for k in
                       ("id", "guid", "link", "title", "published",
                        "updated", "summary", "tags", "author", "category")}
                if db.insert_event(
                    conn,
                    guid=guid, source_id=src_id, title=title,
                    summary=summary, link=link, published=published, raw=raw,
                ):
                    new_count += 1
    except Exception as e:  # noqa: BLE001 — log and move on to next source
        err = f"{type(e).__name__}: {e}"
    finally:
        db.finish_run(conn, run_id, new_count=new_count,
                      seen_count=seen_count, error=err)
        db.mark_source_polled(conn, src_id, error=err)

    return {"source": name, "new": new_count, "seen": seen_count, "error": err}


def harvest_all(conn) -> list[dict]:
    """Poll every enabled source in turn. Returns per-source counters."""
    cur = conn.execute(
        "SELECT * FROM sources WHERE enabled = 1 ORDER BY id"
    )
    sources = list(cur.fetchall())
    results: list[dict] = []
    for src in sources:
        results.append(harvest_one_source(conn, src))
    return results


def _format_age(epoch: int | None) -> str:
    if not epoch:
        return "never"
    age = int(time.time()) - epoch
    if age < 60: return f"{age}s ago"
    if age < 3600: return f"{age // 60}m ago"
    if age < 86400: return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


def cli() -> None:
    ap = argparse.ArgumentParser(prog="indicted-harvester")
    ap.add_argument("--seed", action="store_true",
                    help="Ensure default federal feeds exist, then exit.")
    ap.add_argument("--stats", action="store_true",
                    help="Print DB stats and exit.")
    ap.add_argument("--once", action="store_true",
                    help="Single harvest pass (default).")
    args = ap.parse_args()

    conn = db.connect()
    # Always seed on first ever run, even without --seed.
    n_sources = conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    if n_sources == 0 or args.seed:
        added = seed_default_sources(conn)
        print(f"seeded {added} new source(s); total now: "
              f"{conn.execute('SELECT COUNT(*) AS n FROM sources').fetchone()['n']}")
        if args.seed:
            return

    if args.stats:
        s = db.stats(conn)
        print(f"Crime DB: {db.DB_PATH}")
        print(f"  sources enabled : {s['sources_enabled']}")
        print(f"  events total    : {s['total']}")
        for status in ("new", "scored", "researched", "scripted",
                       "produced", "approved", "published", "rejected", "skipped"):
            print(f"    {status:>10} : {s.get(status, 0)}")
        print(f"  latest event    : {_format_age(s['latest_event'])}")
        print()
        print("Sources:")
        for row in conn.execute("SELECT * FROM sources ORDER BY id"):
            flag = "✓" if row["enabled"] else "○"
            err = f"  ⚠ {row['last_error']}" if row["last_error"] else ""
            print(f"  {flag} {row['name']:<18} {row['kind']:<5} "
                  f"polled {_format_age(row['last_polled'])}{err}")
        return

    print(f"harvesting from {n_sources or len(DEFAULT_FEEDS)} source(s)…")
    started = time.time()
    results = harvest_all(conn)
    elapsed = time.time() - started
    total_new = sum(r["new"] for r in results)
    total_seen = sum(r["seen"] for r in results)
    print(f"\ndone in {elapsed:.1f}s — {total_new} new / {total_seen} seen")
    for r in results:
        flag = "⚠" if r["error"] else "✓"
        msg = f"  {flag} {r['source']:<18} new={r['new']:<4} seen={r['seen']}"
        if r["error"]:
            msg += f"  err: {r['error']}"
        print(msg)


if __name__ == "__main__":
    cli()
