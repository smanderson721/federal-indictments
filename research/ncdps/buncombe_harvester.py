"""Harvest Buncombe County convictions from NCDPS bulk downloads.

Strategy:
    1. Download OFNT3BB1 (Court Commitment) — gives county per commitment.
       Skipped if NCDPS returns 304 Not Modified vs our cached copy.
    2. Stream-parse, keeping rows where CMCOUNTY == BUNCOMBE_NAME.
    3. Join against OFNT3AA1 (Offender Profile) for birth date / gender /
       race details (smaller file, loaded into a dict).
    4. Upsert into convictions table. New rows get status=new.

Returns a summary {"checked": n, "matched": n, "new": n, "skipped": n}.
"""

from __future__ import annotations

from . import bulk_download, db
from .parser import iter_zip_records

# Buncombe County is stored as "BUNCOMBE" in the CMCOUNTY field (uppercased,
# left-padded with spaces in the fixed-width record; .strip() removes them).
BUNCOMBE_COUNTY = "BUNCOMBE"


def _load_offender_profiles() -> dict[str, dict]:
    """Build {opus_id -> {birth_date, gender, race}} from OFNT3AA1.
    OFNT3AA1 has one row per offender ID. Held entirely in memory
    (~35 MB zip → ~400 MB rows). On a GH Actions runner this is fine."""
    print("  [buncombe] loading Offender Profile (OFNT3AA1)...", flush=True)
    zip_path = bulk_download.download_if_modified(
        bulk_download.TABLE_OFFENDER_PROFILE)
    out: dict[str, dict] = {}
    n = 0
    for row in iter_zip_records(zip_path):
        opus = row.get("CMDORNUM", "").strip()
        if not opus:
            continue
        out[opus] = {
            "birth_date": row.get("CMCLBRTH", ""),
            "gender": row.get("CMCLSEX", ""),
            "race": row.get("CMCLRACE", ""),
        }
        n += 1
    print(f"  [buncombe] indexed {n:,} offender profiles", flush=True)
    return out


def harvest_buncombe(conn) -> dict:
    """Scan the Court Commitment table for Buncombe County convictions,
    upsert new ones into the DB. Returns a summary dict."""
    print("  [buncombe] loading Court Commitment (OFNT3BB1)...", flush=True)
    commit_zip = bulk_download.download_if_modified(
        bulk_download.TABLE_COURT_COMMITMENT)

    # Don't load profile detail until we know we have matches (lets us
    # skip the second download when nothing's new).
    profiles: dict[str, dict] | None = None

    summary = {"checked": 0, "matched": 0, "new": 0, "skipped_dupes": 0}

    for row in iter_zip_records(commit_zip):
        summary["checked"] += 1
        county = (row.get("CMCOUNTY") or "").strip().upper()
        if county != BUNCOMBE_COUNTY:
            continue

        # Skip NCDPS legacy placeholder rows — fields filled with `?`
        # characters indicate the data was never properly entered. Real
        # active convictions have a clean status flag like 'ACTIVE'.
        status = (row.get("CMSTAFLG") or "").strip()
        if not status or "?" in status:
            continue
        summary["matched"] += 1

        opus = (row.get("CMDORNUM") or "").strip()
        if not opus:
            continue

        # Avoid loading profiles if we already have this opus_id.
        if db.get_by_id(conn, opus):
            summary["skipped_dupes"] += 1
            continue

        if profiles is None:
            profiles = _load_offender_profiles()

        prof = profiles.get(opus, {})
        record = {
            "opus_id": opus,
            "last_name": (row.get("CMSCLSTN") or "").strip(),
            "first_name": (row.get("CMSCFSTN") or "").strip(),
            "middle_name": (row.get("CMSCMIDN") or "").strip(),
            "birth_date": prof.get("birth_date", ""),
            "gender": prof.get("gender", ""),
            "race": prof.get("race", ""),
            "county": county,
            "admission_date": (row.get("CMADMDTE") or "").strip(),
            "sentence_effective_date":
                (row.get("GMSEFFDT") or "").strip(),
            "most_serious_offense_code":
                (row.get("CMPRIOFF") or "").strip(),
            "sentence_length_months":
                (row.get("CMMAXLEN") or "").strip(),
            "commitment_status": (row.get("CMSTAFLG") or "").strip(),
        }

        if db.upsert(conn, record):
            summary["new"] += 1

    conn.commit()
    return summary
