"""
Indicted crime scorer.

Reads events from the crime DB with status='new', sends them to Gemini
Flash-Lite in batches, and writes back four sub-scores + a composite
0-100 + a blurb. The pipeline then picks the top N per day for
research/scripting.

Score axes (each 0-10, summed × 2.5 → 0-100):

    severity            — how serious the conduct is (administrative
                          press release = 0, fraud = 4, violent crime
                          = 7, terrorism/mass-casualty = 10).
    notability          — public-interest weight (named celebrity,
                          unusual scheme, large dollar figure, novel
                          jurisdiction, federal-first conviction).
    visual_feasibility  — can we produce a watchable 60-second vertical
                          video from this? Named defendant (mugshot
                          plausible), specific location (street view),
                          court documents accessible.
    monetization_safety — YouTube-ad-friendly? Financial fraud / public
                          corruption / drug trafficking score high.
                          Anything CSAM-adjacent, gratuitous violence,
                          or victim-identifying scores low and triggers
                          a 'skip' verdict regardless of other axes.

Verdict:
    'include' — proceed to researcher
    'skip'    — non-news, juvenile defendant, victim-anonymity risk,
                CSAM-adjacent, or duplicate of an existing case

Usage:
    python pipeline.py --crime-score                  # score all new events
    python pipeline.py --crime-score --limit 50       # cap batch size
    python -m research.indicted.scorer                # same, module-direct
"""

from __future__ import annotations

import argparse
import json
import re
import time

import config

from . import db


BATCH_SIZE = 10        # events per Gemini call
MAX_RETRIES = 3
RETRY_BACKOFF = 3.0    # seconds, multiplied by attempt


SCORING_PROMPT = """You are the editorial gate for a YouTube channel called \
"Indicted" that produces 60-second vertical news shorts about federal \
criminal cases in the United States. You will be shown a batch of recent \
federal press releases (DOJ + FBI). Score each one for video-production \
suitability.

For EACH event, output four sub-scores 0-10 and a verdict:

  severity            0 = not a crime story (admin notice, training event,
                          policy memo, statistical release)
                      4 = financial fraud, white-collar
                      6 = drug trafficking, public corruption
                      7 = violent crime, weapons trafficking
                      8 = organized crime, large-scale conspiracy
                     10 = terrorism, mass casualty, national-security

  notability          0 = routine local indictment, unnamed defendants
                      5 = named defendant, mid-sized loss/scheme
                      8 = celebrity / public figure, novel scheme,
                          unprecedented charges
                     10 = nationally-covered, household-name case

  visual_feasibility  0 = nothing concrete (anonymous co-conspirators,
                          no location, no documents cited)
                      5 = named defendant + jurisdiction + court doc
                      8 = + identifiable location for Street View +
                          public photos likely available
                     10 = + already-published mugshot likely + multiple
                          named cooperators

  monetization_safety 0 = CSAM-adjacent, child exploitation, graphic
                          sexual violence, victim-identifying details
                      3 = murder/violent crime with graphic facts
                      7 = drug trafficking, weapons, immigration
                      9 = financial fraud, public corruption, cyber,
                          tax evasion
                     10 = fully advertiser-friendly

Verdict rules:
  - 'skip' if monetization_safety <= 2 (CSAM, victim-identifying sexual)
  - 'skip' if severity == 0 (not a crime story)
  - 'skip' if the press release is about a JUVENILE defendant
  - 'skip' if it's a PSA, conference announcement, or appointment
  - 'skip' if the defendant is unnamed AND no other specific hook
  - otherwise 'include'

Output STRICT JSON, no markdown, no preamble:

{
  "scores": [
    {
      "id": <event_id from input>,
      "severity": N,
      "notability": N,
      "visual_feasibility": N,
      "monetization_safety": N,
      "verdict": "include" | "skip",
      "skip_reason": "<one short phrase if skipped, else empty>",
      "blurb": "<one sentence summarizing the case in active voice, name first>"
    },
    ...
  ]
}

Events to score:
"""


def _format_batch(rows: list) -> str:
    """Render a SQLite event-row list as the bulleted input block."""
    parts: list[str] = []
    for r in rows:
        title = (r["title"] or "").strip()
        summary = (r["summary"] or "").strip()
        # Keep the payload tight — Flash-Lite handles 10 events × ~600 tokens easily.
        if len(summary) > 800:
            summary = summary[:800] + "…"
        parts.append(
            f"--- id={r['id']} ---\n"
            f"TITLE: {title}\n"
            f"SUMMARY: {summary}\n"
        )
    return "\n".join(parts)


def _call_gemini(client, prompt: str) -> dict | None:
    """One Gemini call with retries. Returns parsed JSON or None."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_SCORING,
                contents=prompt,
            )
            text_parts = [
                p.text for p in resp.candidates[0].content.parts
                if hasattr(p, "text") and p.text
            ]
            text = "\n".join(text_parts).strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:  # noqa: BLE001
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
            else:
                print(f"  ⚠ Gemini call failed after {MAX_RETRIES} attempts: {e}")
    return None


def _composite(scores: dict) -> int:
    """Sum four axes (each 0-10) into a 0-100 total."""
    keys = ("severity", "notability", "visual_feasibility", "monetization_safety")
    total = sum(int(scores.get(k, 0)) for k in keys)  # 0-40
    return int(round(total * 2.5))                     # 0-100


def _apply_score(conn, event_id: int, payload: dict) -> str:
    """Write one event's scoring result. Returns the new status."""
    verdict = (payload.get("verdict") or "").lower().strip()
    skip_reason = (payload.get("skip_reason") or "").strip()
    blurb = (payload.get("blurb") or "").strip()
    composite = _composite(payload)

    if verdict == "skip":
        new_status = db.STATUS_SKIPPED
    else:
        new_status = db.STATUS_SCORED

    conn.execute(
        "UPDATE events SET status=?, score=?, score_blurb=?, score_data=? "
        "WHERE id=?",
        (new_status, composite,
         blurb or skip_reason or None,
         json.dumps(payload),
         event_id),
    )
    return new_status


def score_new_events(conn, *, limit: int = 200, batch_size: int = BATCH_SIZE,
                     verbose: bool = True) -> dict:
    """Score up to `limit` events with status='new'. Returns counters."""
    from google import genai
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    cur = conn.execute(
        "SELECT id, title, summary FROM events "
        "WHERE status = ? ORDER BY published DESC NULLS LAST, id DESC LIMIT ?",
        (db.STATUS_NEW, limit),
    )
    rows = list(cur.fetchall())
    if not rows:
        if verbose:
            print("no events with status='new' to score")
        return {"scored": 0, "skipped": 0, "failed": 0, "total": 0}

    if verbose:
        print(f"scoring {len(rows)} event(s) in batches of {batch_size}…")

    scored = skipped = failed = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        batch_ids = {r["id"] for r in batch}
        prompt = SCORING_PROMPT + _format_batch(batch)

        result = _call_gemini(client, prompt)
        if not result or "scores" not in result:
            if verbose:
                print(f"  batch {i // batch_size + 1}: ⚠ no valid response")
            failed += len(batch)
            continue

        returned_ids: set[int] = set()
        for entry in result["scores"]:
            try:
                eid = int(entry["id"])
            except (KeyError, ValueError, TypeError):
                continue
            if eid not in batch_ids:
                continue   # hallucinated id
            returned_ids.add(eid)
            status = _apply_score(conn, eid, entry)
            if status == db.STATUS_SKIPPED:
                skipped += 1
            else:
                scored += 1

        # Anything in batch but missing from result counts as failed.
        missed = batch_ids - returned_ids
        failed += len(missed)
        if missed and verbose:
            print(f"  batch {i // batch_size + 1}: "
                  f"⚠ {len(missed)} event(s) missing from response")

        if verbose:
            done = i + len(batch)
            print(f"  progress: {done}/{len(rows)} "
                  f"(scored={scored}, skipped={skipped}, failed={failed})")

    return {"scored": scored, "skipped": skipped, "failed": failed,
            "total": len(rows)}


def print_top(conn, n: int = 20) -> None:
    """Print the top-N highest-scoring events currently waiting at status='scored'."""
    cur = conn.execute(
        "SELECT id, score, title, score_blurb FROM events "
        "WHERE status = ? ORDER BY score DESC, published DESC LIMIT ?",
        (db.STATUS_SCORED, n),
    )
    rows = list(cur.fetchall())
    if not rows:
        print("no scored events yet (run --crime-score first)")
        return
    print(f"\nTop {len(rows)} scored events awaiting research:")
    for r in rows:
        print(f"  [{r['score']:>3}] #{r['id']}  {(r['title'] or '')[:80]}")
        blurb = (r["score_blurb"] or "").strip()
        if blurb:
            print(f"          → {blurb[:120]}")


def cli() -> None:
    ap = argparse.ArgumentParser(prog="indicted-scorer")
    ap.add_argument("--limit", type=int, default=200,
                    help="Max events to score this run (default 200)")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--top", type=int, metavar="N",
                    help="Just print the top N scored events and exit")
    args = ap.parse_args()

    conn = db.connect()
    if args.top:
        print_top(conn, args.top)
        return

    out = score_new_events(conn, limit=args.limit, batch_size=args.batch_size)
    print(f"\ndone: scored={out['scored']} skipped={out['skipped']} "
          f"failed={out['failed']} (of {out['total']})")
    print_top(conn, 10)


if __name__ == "__main__":
    cli()
