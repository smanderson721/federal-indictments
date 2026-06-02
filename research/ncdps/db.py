"""SQLite store for harvested Buncombe County convictions.

Schema:
    convictions(opus_id PK, last_name, first_name, middle_name,
                birth_date, gender, race, county, admission_date,
                sentence_effective_date, most_serious_offense_code,
                sentence_length_months, commitment_status,
                first_seen_at, project_dir, rendered_at, status)

`status` is one of:
    new        — just harvested, not yet rendered
    rendering  — render workflow dispatched
    rendered   — final mp4 produced + uploaded
    failed     — render failed (don't retry automatically)
    skipped    — manually excluded
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "research_output" / "ncdps" / "buncombe.db"

STATUS_NEW       = "new"
STATUS_RENDERING = "rendering"
STATUS_RENDERED  = "rendered"
STATUS_FAILED    = "failed"
STATUS_SKIPPED   = "skipped"
STATUS_NO_PHOTO  = "skipped_no_photo"
STATUS_NO_CODE   = "skipped_unknown_offense"

SCHEMA = """
CREATE TABLE IF NOT EXISTS convictions (
    opus_id TEXT PRIMARY KEY,
    last_name TEXT,
    first_name TEXT,
    middle_name TEXT,
    birth_date TEXT,
    gender TEXT,
    race TEXT,
    county TEXT,
    admission_date TEXT,
    sentence_effective_date TEXT,
    most_serious_offense_code TEXT,
    sentence_length_months TEXT,
    commitment_status TEXT,
    first_seen_at INTEGER,
    project_dir TEXT,
    rendered_at INTEGER,
    status TEXT
);

CREATE INDEX IF NOT EXISTS idx_conv_eff_date
    ON convictions(sentence_effective_date DESC);
CREATE INDEX IF NOT EXISTS idx_conv_status
    ON convictions(status);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert a conviction if not already present. Returns True if new."""
    existing = conn.execute(
        "SELECT opus_id FROM convictions WHERE opus_id = ?",
        (row["opus_id"],),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """INSERT INTO convictions
           (opus_id, last_name, first_name, middle_name, birth_date,
            gender, race, county, admission_date, sentence_effective_date,
            most_serious_offense_code, sentence_length_months,
            commitment_status, first_seen_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["opus_id"],
            row.get("last_name"),
            row.get("first_name"),
            row.get("middle_name"),
            row.get("birth_date"),
            row.get("gender"),
            row.get("race"),
            row.get("county"),
            row.get("admission_date"),
            row.get("sentence_effective_date"),
            row.get("most_serious_offense_code"),
            row.get("sentence_length_months"),
            row.get("commitment_status"),
            int(time.time()),
            STATUS_NEW,
        ),
    )
    return True


def set_status(conn: sqlite3.Connection, opus_id: str, status: str,
               project_dir: str | None = None) -> None:
    if project_dir:
        conn.execute(
            "UPDATE convictions SET status = ?, project_dir = ?, "
            "rendered_at = ? WHERE opus_id = ?",
            (status, project_dir, int(time.time()), opus_id),
        )
    else:
        conn.execute(
            "UPDATE convictions SET status = ? WHERE opus_id = ?",
            (status, opus_id),
        )


def most_recent_new(conn: sqlite3.Connection) -> dict | None:
    """Return the newest-by-sentence-effective-date `new` conviction."""
    row = conn.execute(
        """SELECT * FROM convictions
           WHERE status = ?
           ORDER BY sentence_effective_date DESC, first_seen_at DESC
           LIMIT 1""",
        (STATUS_NEW,),
    ).fetchone()
    return dict(row) if row else None


def get_by_id(conn: sqlite3.Connection, opus_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM convictions WHERE opus_id = ?", (opus_id,),
    ).fetchone()
    return dict(row) if row else None


def stats(conn: sqlite3.Connection) -> dict:
    out: dict[str, int] = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM convictions GROUP BY status"
    ):
        out[row["status"]] = row["n"]
    out["total"] = sum(out.values())
    return out
