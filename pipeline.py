#!/usr/bin/env python3
"""The Verdict — federal-indictment YouTube Shorts pipeline.

Stages:
    1. harvest    — Poll DOJ/FBI RSS feeds, store new events in crime.db.
    2. score      — Gemini scores each new event 0–100.
    3. research   — Build case_file.json for top-scored events.
    4. script     — Write narration.txt + script.json.
    5. produce    — Render the 1080×1920 Short.

All API keys are read from the GEMINI_API_KEY / ELEVENLABS_API_KEY /
GOOGLE_MAPS_API_KEY environment variables (or .env at repo root).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


def cmd_harvest(args) -> int:
    from research.indicted import db, harvester
    conn = db.connect()
    if conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"] == 0:
        harvester.seed_default_sources(conn)
    results = harvester.harvest_all(conn)
    total = 0
    for r in results:
        n = r.get("new", 0)
        total += n
        err = f"  ERR={r['error']}" if r.get("error") else ""
        print(f"  [{r['source']}] new={n} seen={r.get('seen', 0)}{err}")
    print(f"\n  ✓ Harvest complete: {total} new events.")
    return 0


def cmd_score(args) -> int:
    from research.indicted import db, scorer
    conn = db.connect()
    n = scorer.score_new_events(conn, limit=args.limit)
    print(f"  ✓ Scored {n} events.")
    if args.top:
        scorer.print_top(conn, n=args.top)
    return 0


def cmd_research(args) -> int:
    from research.indicted import db, researcher
    conn = db.connect()
    if args.event_id:
        researcher.research_event_by_id(conn, args.event_id)
    else:
        n = researcher.research_top_scored(conn, limit=args.limit)
        print(f"  ✓ Researched {n} events.")
    return 0


def cmd_script(args) -> int:
    from research.indicted import scripter
    proj = Path(args.project_dir)
    if args.stage in ("narration", "both"):
        scripter.write_narration(proj)
    if args.stage in ("script", "both"):
        scripter.write_script(proj)
    return 0


def cmd_produce(args) -> int:
    from production.convo_video import produce_verdict_video
    out = produce_verdict_video(args.project_dir)
    print(f"  ✓ {out.get('output_path')}")
    return 0


def cmd_list(args) -> int:
    from research.indicted import db
    conn = db.connect()
    for r in db.iter_events_by_status(conn, args.status, limit=args.limit):
        score = r["score"] if r["score"] is not None else "-"
        title = (r["title"] or "")[:80]
        print(f"  [{r['status']:>10s}] score={score:>3} #{r['id']:>5d}  {title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The Verdict pipeline.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("harvest", help="Poll federal news feeds (DOJ, FBI, etc.)").set_defaults(func=cmd_harvest)

    p = sub.add_parser("score", help="Gemini-score new events")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--top", type=int, default=10, help="Print top N after scoring (0 to skip)")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("research", help="Build case_file.json for top-scored events")
    p.add_argument("--limit", type=int, default=6)
    p.add_argument("--event-id", type=int, default=None)
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("script", help="Write narration.txt and/or script.json")
    p.add_argument("project_dir")
    p.add_argument("--stage", choices=["narration", "script", "both"], default="both")
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("produce", help="Render the Short")
    p.add_argument("project_dir")
    p.set_defaults(func=cmd_produce)

    p = sub.add_parser("list", help="List events from the DB by status")
    p.add_argument("--status", default="scored")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
