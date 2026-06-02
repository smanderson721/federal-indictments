"""
Indicted crime researcher.

Reads an event with status='scored', fetches the full press-release
text from the original DOJ/FBI URL, runs Gemini search grounding for
broader context (court documents, related coverage, defendant
background), and writes a structured `case_file.json` to
projects/crime-<case_id>/.

Then advances the event to status='researched' and stamps it with
case_id (the project-folder slug).

Case file schema (versioned, v1):

{
  "version": 1,
  "case_id": "<slug>",
  "event_id": <db id>,
  "harvested_url": "...",
  "score": <0-100>,
  "score_blurb": "...",
  "headline": "...",
  "press_release_text": "<full text>",
  "press_release_url": "...",
  "agency": "doj" | "fbi" | "atf" | "dea",
  "filed_on": "YYYY-MM-DD" | null,

  "defendants": [
    {"name": "...", "age": N|null, "city": "...", "state": "..",
     "role": "...", "convicted": false}
  ],
  "charges": [
    {"defendant": "...", "statute": "18 U.S.C. § ...", "count_name": "..."}
  ],
  "victims": [
    {"category": "person"|"organization"|"government",
     "anonymized_label": "Victim 1", "harm": "..."}
  ],
  "locations": [
    {"city": "...", "state": "..", "lat": null, "lon": null,
     "description": "..."}
  ],
  "court": {"district": "...", "case_no": "...", "judge": "..."},
  "investigators": ["FBI", "..."],
  "key_dates": [{"date": "YYYY-MM-DD", "event": "indictment unsealed"}],
  "dollar_figures": [{"amount_usd": N, "label": "..."}],

  "court_doc_urls": ["https://..."],
  "related_coverage_urls": ["https://..."],
  "image_search_hints": ["mugshot site:fbi.gov", "..."],

  "legal_flags": {
    "juvenile_defendant": false,
    "victim_anonymity_required": false,
    "mugshot_restricted_state": false,   # CA/NY/FL/IL/NJ/UT
    "alleged_only": true                  # not yet convicted
  },

  "narrator_brief": "<2-3 paragraph factual brief, no embellishment>",
  "research_grounding_sources": ["https://...", ...]
}

Usage:
    python pipeline.py --crime-research                 # research top N scored
    python pipeline.py --crime-research --limit 3
    python pipeline.py --crime-research --event-id 142  # specific event
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

import config

from . import db


CASE_FILE_VERSION = 1
USER_AGENT = "IndictedResearcher/1.0 (+https://indicted.goods-live.com)"

PROJECTS_ROOT = Path(__file__).resolve().parent.parent.parent / "projects"

MUGSHOT_RESTRICTED_STATES = {"CA", "NY", "FL", "IL", "NJ", "UT"}


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "case"


def _case_id_for(event_row, defendants: list[dict] | None = None) -> str:
    """Build a unique slug like 'bolton-classified-info-142'."""
    if defendants:
        primary = defendants[0].get("name", "").split()[-1]
        if primary:
            keyword = _slugify(primary, 30)
            return f"{keyword}-{event_row['id']}"
    return _slugify(event_row["title"] or f"case-{event_row['id']}", 40) + f"-{event_row['id']}"


def _fetch_press_release(url: str, timeout: int = 20) -> str:
    """Download and strip a DOJ/FBI press release page to plain text."""
    if not url:
        return ""
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"[fetch error: {type(e).__name__}: {e}]"

    html = resp.text
    # Try beautifulsoup if available for clean extraction; fall back to regex.
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header",
                          "footer", "form", "aside"]):
            tag.decompose()
        # DOJ pages have an article body in .field--name-body or similar;
        # the broad strategy is to take <main> if present.
        main = soup.find("main") or soup.find("article") or soup.body
        text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)
    except ImportError:
        text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:20000]   # 20k chars is enough for any single release


EXTRACTION_PROMPT = """You are an investigative legal researcher. Given the \
FULL TEXT of a US federal press release (DOJ, FBI, ATF, or DEA), extract \
structured facts and assess legal/editorial flags for a YouTube news video.

Press release URL: {url}
Headline: {title}

FULL TEXT:
\"\"\"
{full_text}
\"\"\"

Also use Google Search to find:
  - The court docket / indictment / sentencing memo (PACER, CourtListener,
    docket alarm, or court.gov)
  - Any other reputable news coverage of THIS specific case
  - Background on the named defendant(s) only — never the victims

Return STRICT JSON, no markdown, exactly this schema:

{{
  "agency": "doj" | "fbi" | "atf" | "dea" | "usms",
  "filed_on": "YYYY-MM-DD" or null,
  "defendants": [
    {{"name": "FullName", "age": N or null, "city": "..", "state": "XX",
      "role": "<one-line role>", "convicted": true|false}}
  ],
  "charges": [
    {{"defendant": "FullName", "statute": "<USC cite if mentioned>",
      "count_name": "<plain-English charge>"}}
  ],
  "victims": [
    {{"category": "person"|"organization"|"government",
      "anonymized_label": "Victim 1",
      "harm": "<plain description, no PII>"}}
  ],
  "locations": [
    {{"city": "..", "state": "XX", "lat": null, "lon": null,
      "description": "<why this place matters>"}}
  ],
  "court": {{"district": "<e.g. SDNY>", "case_no": "<or null>", "judge": "<or null>"}},
  "investigators": ["FBI", "IRS-CI", ...],
  "key_dates": [{{"date": "YYYY-MM-DD", "event": "<short label>"}}],
  "dollar_figures": [{{"amount_usd": N, "label": "<what the amount represents>"}}],

  "court_doc_urls": ["https://..."],
  "related_coverage_urls": ["https://..."],
  "image_search_hints": ["<query you'd google for a photo>", ...],

  "legal_flags": {{
    "juvenile_defendant": true|false,
    "victim_anonymity_required": true|false,
    "mugshot_restricted_state": true|false,
    "alleged_only": true|false
  }},

  "narrator_brief": "<2-3 short paragraphs of dry factual narration brief, \
present tense, no editorializing>"
}}

Rules:
  - Use 'allegedly' framing throughout unless the press release explicitly \
says a defendant pleaded guilty or was convicted.
  - Never include victim names. Refer to victims by their anonymized_label.
  - mugshot_restricted_state is true if any defendant's state is in \
{{CA, NY, FL, IL, NJ, UT}}.
  - If a field is unknown, return [] or null — do NOT guess.
"""


def _research_one(client, event_row, full_text: str) -> dict | None:
    """One Gemini extraction + grounding call. Returns parsed JSON dict
    plus a 'research_grounding_sources' list, or None on failure."""
    from google import genai
    from google.genai import types

    prompt = EXTRACTION_PROMPT.format(
        url=event_row["link"] or "",
        title=event_row["title"] or "",
        full_text=full_text or (event_row["summary"] or ""),
    )

    search_tool = types.Tool(google_search=types.GoogleSearch())
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_RESEARCH,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[search_tool]),
            )
            text_parts = [
                p.text for p in resp.candidates[0].content.parts
                if hasattr(p, "text") and p.text
            ]
            text = "\n".join(text_parts).strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError("no JSON found in response")
            data = json.loads(match.group())

            # Capture grounding source URLs (if any).
            sources: list[str] = []
            try:
                gm = resp.candidates[0].grounding_metadata
                if gm and getattr(gm, "grounding_chunks", None):
                    for ch in gm.grounding_chunks:
                        if hasattr(ch, "web") and ch.web and ch.web.uri:
                            sources.append(ch.web.uri)
            except Exception:
                pass
            data["research_grounding_sources"] = sources
            return data
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
            else:
                print(f"  ⚠ extraction failed: {type(e).__name__}: {e}")
    return None


def _post_process_legal_flags(data: dict) -> None:
    """Belt-and-suspenders enforcement of editorial rules."""
    flags = data.setdefault("legal_flags", {})

    # mugshot-restricted state: enforce based on actual state codes
    flags.setdefault("mugshot_restricted_state", False)
    for d in data.get("defendants", []):
        st = (d.get("state") or "").upper().strip()
        if st in MUGSHOT_RESTRICTED_STATES:
            flags["mugshot_restricted_state"] = True
            break

    # alleged_only: true unless any defendant.convicted
    flags["alleged_only"] = not any(
        d.get("convicted") for d in data.get("defendants", [])
    )


def research_one_event(conn, event_row, verbose: bool = True) -> dict | None:
    """Build a case file for a single event. Returns the case file dict,
    or None on failure. Writes the file to disk and advances the event row."""
    from google import genai
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    if verbose:
        print(f"  → fetching press release: {event_row['link']}")
    full_text = _fetch_press_release(event_row["link"] or "")
    if not full_text or full_text.startswith("[fetch error"):
        if verbose:
            print(f"    ⚠ press-release fetch failed; using summary only")
        full_text = event_row["summary"] or ""

    if verbose:
        print(f"  → extracting facts via Gemini ({len(full_text)} chars)…")
    extracted = _research_one(client, event_row, full_text)
    if not extracted:
        return None

    _post_process_legal_flags(extracted)

    case_id = _case_id_for(event_row, extracted.get("defendants"))
    project_dir = PROJECTS_ROOT / f"crime-{case_id}"
    project_dir.mkdir(parents=True, exist_ok=True)

    score_data = {}
    if event_row["score_data"]:
        try:
            score_data = json.loads(event_row["score_data"])
        except Exception:
            pass

    case_file = {
        "version": CASE_FILE_VERSION,
        "case_id": case_id,
        "event_id": event_row["id"],
        "harvested_url": event_row["link"],
        "score": event_row["score"],
        "score_blurb": event_row["score_blurb"],
        "score_breakdown": {
            k: score_data.get(k) for k in
            ("severity", "notability", "visual_feasibility",
             "monetization_safety")
        } if score_data else None,
        "headline": event_row["title"],
        "press_release_url": event_row["link"],
        "press_release_text": full_text,
        **extracted,
    }

    case_file_path = project_dir / "case_file.json"
    case_file_path.write_text(json.dumps(case_file, indent=2,
                                          ensure_ascii=False))

    conn.execute(
        "UPDATE events SET status=?, case_id=? WHERE id=?",
        (db.STATUS_RESEARCHED, case_id, event_row["id"]),
    )

    if verbose:
        print(f"  ✓ {case_file_path.relative_to(PROJECTS_ROOT.parent)}")
        defendants = ", ".join(d.get("name", "?")
                               for d in extracted.get("defendants", [])[:3])
        if defendants:
            print(f"    defendants: {defendants}")
        flags = extracted.get("legal_flags", {})
        flag_list = [k for k, v in flags.items() if v]
        if flag_list:
            print(f"    ⚐ flags: {', '.join(flag_list)}")

    return case_file


def research_top_scored(conn, *, limit: int = 6, verbose: bool = True) -> int:
    """Research the top-N scored events (highest score first).
    Returns count of successfully researched events."""
    cur = conn.execute(
        "SELECT * FROM events WHERE status = ? "
        "ORDER BY score DESC, published DESC LIMIT ?",
        (db.STATUS_SCORED, limit),
    )
    rows = list(cur.fetchall())
    if not rows:
        if verbose:
            print("no events with status='scored' to research")
        return 0

    if verbose:
        print(f"researching {len(rows)} top-scored event(s)…\n")

    ok = 0
    for i, row in enumerate(rows, 1):
        if verbose:
            print(f"[{i}/{len(rows)}] #{row['id']} score={row['score']} "
                  f"{(row['title'] or '')[:70]}")
        result = research_one_event(conn, row, verbose=verbose)
        if result:
            ok += 1
    return ok


def research_event_by_id(conn, event_id: int) -> dict | None:
    cur = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    row = cur.fetchone()
    if not row:
        print(f"⚠ event id {event_id} not found")
        return None
    return research_one_event(conn, row)


def cli() -> None:
    ap = argparse.ArgumentParser(prog="indicted-researcher")
    ap.add_argument("--limit", type=int, default=3,
                    help="Number of top-scored events to research (default 3)")
    ap.add_argument("--event-id", type=int,
                    help="Research one specific event id")
    args = ap.parse_args()

    conn = db.connect()
    if args.event_id:
        research_event_by_id(conn, args.event_id)
    else:
        n = research_top_scored(conn, limit=args.limit)
        print(f"\n✓ researched {n} event(s)")


if __name__ == "__main__":
    cli()
