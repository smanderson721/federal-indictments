"""
SQLite schema + helpers for the Indicted federal-crime pipeline.

Database lives at: research_output/indicted/crime.db

Tables:
    sources   — Registered federal news feeds (DOJ, FBI, etc.)
    events    — One row per harvested press release / RSS entry.
                State machine via .status: new → scored → researched →
                scripted → produced → approved → published, or rejected/skipped.
    runs      — Per-harvest-pass bookkeeping for debugging + rate-limit math.

All timestamps stored as Unix epoch seconds (integer).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "research_output" / "indicted"
DB_PATH = DB_DIR / "crime.db"


# Event status state machine. Workers pick up events in `WHERE status = ?`
# order and advance them through the pipeline.
STATUS_NEW = "new"
STATUS_SKIPPED = "skipped"        # filtered out by legal/scope rules
STATUS_SCORED = "scored"          # crime_scorer ran; score + blurb populated
STATUS_RESEARCHED = "researched"  # case_file.json built
STATUS_SCRIPTED = "scripted"      # narration + script.json written
STATUS_PRODUCED = "produced"      # MP4 rendered, waiting for review
STATUS_APPROVED = "approved"      # user clicked Approve in Videos tab → upload queue
STATUS_PUBLISHED = "published"    # uploaded to YouTube
STATUS_REJECTED = "rejected"      # user clicked Reject

ALL_STATUSES = (
    STATUS_NEW, STATUS_SKIPPED, STATUS_SCORED, STATUS_RESEARCHED,
    STATUS_SCRIPTED, STATUS_PRODUCED, STATUS_APPROVED, STATUS_PUBLISHED,
    STATUS_REJECTED,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    url          TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- doj, fbi, dea, atf, ice, usms
    jurisdiction TEXT,                    -- 'federal', or state/district code
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_polled  INTEGER,
    last_error   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    guid        TEXT UNIQUE NOT NULL,     -- entry GUID or canonical link
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    title       TEXT,
    summary     TEXT,
    link        TEXT,
    published   INTEGER,                  -- epoch from feed
    fetched     INTEGER NOT NULL,         -- epoch when we first saw it
    raw         TEXT,                     -- JSON dump of full feedparser entry
    status      TEXT NOT NULL DEFAULT 'new',
    score       INTEGER,                  -- 0-100 (NULL until scorer runs)
    score_blurb TEXT,
    score_data  TEXT,                     -- JSON breakdown from scorer
    case_id     TEXT                      -- slug for projects/crime-<slug>/
);

CREATE INDEX IF NOT EXISTS idx_events_status     ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_published  ON events(published);
CREATE INDEX IF NOT EXISTS idx_events_score      ON events(score);
CREATE INDEX IF NOT EXISTS idx_events_source     ON events(source_id);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started     INTEGER NOT NULL,
    ended       INTEGER,
    source_id   INTEGER,
    new_count   INTEGER NOT NULL DEFAULT 0,
    seen_count  INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
"""


def connect() -> sqlite3.Connection:
    """Open (and migrate) the crime database. Returns a fresh connection
    with row_factory set so callers get dict-like rows."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_source(conn: sqlite3.Connection, *, name: str, url: str,
                  kind: str, jurisdiction: str | None = "federal",
                  enabled: bool = True) -> int:
    """Insert a source if missing, return its id. Does not overwrite
    existing rows (so manual edits via SQL survive re-seeding)."""
    cur = conn.execute("SELECT id FROM sources WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources (name, url, kind, jurisdiction, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, url, kind, jurisdiction, 1 if enabled else 0),
    )
    return cur.lastrowid


def insert_event(conn: sqlite3.Connection, *, guid: str, source_id: int,
                 title: str, summary: str, link: str,
                 published: int | None, raw: dict) -> bool:
    """Insert one event row. Returns True if it was new, False if already
    present (UNIQUE constraint on guid)."""
    try:
        conn.execute(
            "INSERT INTO events (guid, source_id, title, summary, link, "
            "                    published, fetched, raw, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guid, source_id, title, summary, link, published,
             int(time.time()), json.dumps(raw, default=str), STATUS_NEW),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def start_run(conn: sqlite3.Connection, source_id: int | None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started, source_id) VALUES (?, ?)",
        (int(time.time()), source_id),
    )
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, *,
               new_count: int, seen_count: int,
               error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET ended=?, new_count=?, seen_count=?, error=? "
        "WHERE id=?",
        (int(time.time()), new_count, seen_count, error, run_id),
    )


def mark_source_polled(conn: sqlite3.Connection, source_id: int,
                       error: str | None = None) -> None:
    conn.execute(
        "UPDATE sources SET last_polled=?, last_error=? WHERE id=?",
        (int(time.time()), error, source_id),
    )


def stats(conn: sqlite3.Connection) -> dict:
    """Return overview stats for CLI / dashboard."""
    out: dict = {}
    for status in ALL_STATUSES:
        cur = conn.execute("SELECT COUNT(*) AS n FROM events WHERE status = ?", (status,))
        out[status] = cur.fetchone()["n"]
    cur = conn.execute("SELECT COUNT(*) AS n FROM events")
    out["total"] = cur.fetchone()["n"]
    cur = conn.execute("SELECT COUNT(*) AS n FROM sources WHERE enabled = 1")
    out["sources_enabled"] = cur.fetchone()["n"]
    cur = conn.execute(
        "SELECT MAX(published) AS p FROM events WHERE published IS NOT NULL"
    )
    p = cur.fetchone()["p"]
    out["latest_event"] = int(p) if p else None
    return out


def iter_events_by_status(conn: sqlite3.Connection, status: str,
                          limit: int = 100) -> Iterable[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM events WHERE status = ? ORDER BY published DESC LIMIT ?",
        (status, limit),
    )
    yield from cur
