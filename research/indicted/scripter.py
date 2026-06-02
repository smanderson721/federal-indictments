"""Scripter for The Verdict (Pipeline 0 — federal-crime YouTube Shorts).

Two-step workflow:

    Step A — narration
        $ python -m research.indicted.scripter narration projects/crime-<slug>

        Reads:
            • case_file.json         (the indictment / press release)
            • research_expansion.json (two-round Q&A backstory; run
                                       expander first if missing — this
                                       script will auto-run it)

        Writes:
            • narration.txt          (8-10 scene-tagged blocks the user
                                       reviews and edits in place)

    Step B — script
        $ python -m research.indicted.scripter script projects/crime-<slug>

        Reads narration.txt (post-approval) + case + expansion.
        Writes script.json — a scene-by-scene production script keyed
        to the 6 layouts in d3_scenes/vertical_indicted.html.

Voice: Aoede (American newsroom preamble, ~155 WPM). Target runtime is
~60 seconds → ~155 words of narration → ~18 words per scene.

The narration MUST weave concrete factual material from BOTH Q&A
answers in research_expansion.json so the viewer leaves informed about
what actually happened — not just that someone was charged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterator

import config


# ── Layout vocabulary (must match d3_scenes/vertical_indicted.html) ──

LAYOUTS = [
    "breaking_intro", "doc_screenshot", "mugshot_card",
    "street_view", "quote_card", "takeaway",
]

LAYOUT_GUIDE = """\
LAYOUT VOCABULARY (use these exact strings):

  breaking_intro   Opening scene. Big red banner + headline + defendant
                   block + animated ticker. Use ONCE, always first.
                   Card fields: agency, status_tag, headline,
                                defendant_name, defendant_role,
                                defendant_location, ticker.

  doc_screenshot   Court document / press release image with a
                   highlighted region and a typewriter quote pulled
                   from the document. Use for primary-source moments.
                   Card fields: source_label, image_path, image_caption,
                                highlight {x,y,w,h normalized 0-1},
                                quote.

  mugshot_card     Defendant photo (portrait crop) with name banner and
                   a wrapped list of up to 5 charge labels. Use for
                   "who is this person + what are they charged with".
                   Card fields: defendant_name, location, source_credit,
                                image_path, charges[], alleged_only.
                   *** SKIP this layout entirely if legal_flags
                       .mugshot_restricted_state is true. ***

  street_view      Full-frame Google Street View image with location
                   pin + event/date overlays. Use for "where it
                   happened" beats (courthouse, residence searched).
                   Card fields: image_path, location_label, event_label,
                                date_label.

  quote_card       Big open-quote glyph + body quote + attribution.
                   Use sparingly — only for a single high-impact quote
                   (DOJ official, court doc, defendant statement).
                   Card fields: quote, attribution.

  takeaway         Closing scene. Centered statement + red underline +
                   amber footer + channel block. Use ONCE, always last.
                   Card fields: text, footer, channel_handle.
"""


# ── Helpers ──────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _ensure_expansion(project_dir: Path, case: dict) -> dict:
    exp_path = project_dir / "research_expansion.json"
    if exp_path.is_file():
        return _load_json(exp_path)
    print("[scripter] no research_expansion.json — running expander first")
    from research.indicted.expander import expand_case_research
    return expand_case_research(case, project_dir=project_dir)


def _strip_codefence(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _text_of(resp) -> str:
    return "".join(
        p.text for p in resp.candidates[0].content.parts
        if getattr(p, "text", None)
    ).strip()


def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "")).strip("-").lower()
    return s or "verdict"


# ── Step A: narration generation ─────────────────────────────────────

_NARRATION_PROMPT = """\
You are the head writer for THE VERDICT (@TheVerdict_USA), a vertical
YouTube Shorts channel covering federal criminal indictments. Voice:
Aoede, American newsroom delivery, ~155 WPM.

Write the narration for one 60-second short.

Target: 8-10 scenes, ~150-165 words TOTAL. Each scene is one short
spoken block (12-22 words). Do NOT exceed 165 words across the whole
script — the voice talent paces at 155 WPM and the video is 60s hard.

Hard rules:
  • Direct, declarative newsroom voice. No filler. No "Here's the
    thing", "It turns out", "Now,", "But here's where it gets...",
    no second-person address ("you may have heard").
  • Use "allegedly" / "prosecutors allege" / "the indictment alleges"
    framing throughout if legal_flags.alleged_only is true.
  • The narration MUST weave in concrete factual material from BOTH
    viewer-Q&A answers below. Those answers contain the backstory the
    primer alone doesn't reveal (e.g. the Iranian hack, the memoir
    motive, who was targeted, how it was uncovered). Without them the
    viewer is confused; with them the viewer is informed.
  • Specific over generic. Use names, dates, dollar figures, page
    counts, counts of charges — the facts that are in the case file
    and Q&A — instead of vague phrases.
  • Open scene 1 with `breaking_intro`. Close the final scene with
    `takeaway`.
  • Pick layouts that fit the content of each beat:
        - breaking_intro      → headline + defendant
        - doc_screenshot      → primary-source detail with a short
                                pull-quote (5-12 words)
        - mugshot_card        → "who is this person + charges"
                                *** OMIT entirely if
                                legal_flags.mugshot_restricted_state
                                is true ***
        - street_view         → location beat (residence searched,
                                courthouse)
        - quote_card          → ONE high-impact official quote
        - takeaway            → closing thesis line
  • Do NOT repeat the same layout back-to-back (except that a
    doc_screenshot can be followed by a different layout, then another
    doc_screenshot later).

{layout_guide}

CASE FILE (parsed indictment data):
{case_json}

VIEWER Q&A (substantive backstory — weave these facts into the
narration):
{qa_block}

OUTPUT FORMAT — exactly this, nothing else. One blank line between
scenes. The tag inside the brackets is `[id | layout]` and the
narration follows on the next line(s):

[s001 | breaking_intro]
Federal grand jury indicts former National Security Advisor John
Bolton on eighteen counts of mishandling classified intelligence.

[s002 | doc_screenshot]
The indictment alleges Bolton transmitted over one thousand pages of
Top Secret material...

...and so on, ending with `[sNNN | takeaway]`.
"""


def _build_qa_block(expansion: dict) -> str:
    qa = expansion.get("viewer_qa") or []
    if not qa:
        return "(no expansion available)"
    parts = []
    for i, item in enumerate(qa, 1):
        parts.append(
            f"Q{i}: {item.get('question','').strip()}\n"
            f"A{i}: {item.get('answer','').strip()}"
        )
    return "\n\n".join(parts)


def _case_for_prompt(case: dict) -> str:
    """Strip the bulky press_release_text — the narrator_brief and
    structured fields are what the writer needs."""
    keys = [
        "case_id", "headline", "agency", "filed_on", "defendants",
        "charges", "victims", "locations", "court", "investigators",
        "key_dates", "dollar_figures", "legal_flags", "narrator_brief",
    ]
    sub = {k: case.get(k) for k in keys if k in case}
    return json.dumps(sub, indent=2)


def write_narration(project_dir: Path) -> Path:
    case_path = project_dir / "case_file.json"
    if not case_path.is_file():
        raise SystemExit(f"no case_file.json at {case_path}")
    case = _load_json(case_path)
    expansion = _ensure_expansion(project_dir, case)

    import os
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)

    prompt = _NARRATION_PROMPT.format(
        layout_guide=LAYOUT_GUIDE,
        case_json=_case_for_prompt(case),
        qa_block=_build_qa_block(expansion),
    )

    print(f"[scripter] writing narration ({config.MODEL_SCRIPTWRITING})")
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_SCRIPTWRITING,
                contents=prompt,
            )
            text = _text_of(resp)
            break
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                print(f"  retry after {type(e).__name__}: {e}")
                time.sleep(3 * (attempt + 1))
            else:
                raise

    # Light sanity check: must contain `[s001 |` and `takeaway`
    if "[s001" not in text or "takeaway" not in text.lower():
        print("[scripter] WARNING: output missing s001 or takeaway tag")

    out = project_dir / "narration.txt"
    out.write_text(text.rstrip() + "\n")
    word_count = len(re.findall(r"\b\w+\b",
                                re.sub(r"\[s\d+\s*\|[^\]]+\]", "", text)))
    scene_count = len(re.findall(r"^\[s\d+\s*\|", text, re.MULTILINE))
    print(f"[scripter] wrote {out}")
    print(f"           scenes: {scene_count}   words: {word_count} "
          f"(target 150-165 ≈ 60s at 155 WPM)")
    print("           → review and edit, then run:")
    print(f"             python -m research.indicted.scripter script "
          f"{project_dir}")
    return out


# ── Step B: script.json generation ───────────────────────────────────

_SCENE_TAG_RE = re.compile(
    r"^\[(s\d+)\s*\|\s*([a-z_]+)\]\s*$", re.MULTILINE,
)


def _parse_narration(text: str) -> list[dict]:
    """Parse `[sNNN | layout]` blocks into a list of
    {id, layout, narration}. Blocks are separated by blank lines."""
    scenes: list[dict] = []
    cur_id: str | None = None
    cur_layout: str | None = None
    cur_lines: list[str] = []

    def flush() -> None:
        if cur_id and cur_layout:
            scenes.append({
                "id": cur_id,
                "layout": cur_layout,
                "narration": " ".join(
                    ln.strip() for ln in cur_lines if ln.strip()
                ).strip(),
            })

    for line in text.splitlines():
        m = _SCENE_TAG_RE.match(line)
        if m:
            flush()
            cur_lines = []
            cur_id = m.group(1)
            cur_layout = m.group(2)
        else:
            cur_lines.append(line)
    flush()
    return scenes


_SCRIPT_PROMPT = """\
You are converting an approved narration into a production-ready
script.json for THE VERDICT (vertical 1080x1920, 30fps, ffmpeg-rendered
via d3_scenes/vertical_indicted.html).

For EACH scene below you have:
  • id              (e.g. "s001")
  • layout          (one of the 6 layouts)
  • narration       (spoken text — KEEP IT VERBATIM)

Your job: emit a JSON object with the production-ready card config for
each scene that matches its layout's required fields. Do NOT alter
the narration text.

{layout_guide}

GLOBAL RULES:
  • Use accent "#dc2626" (Verdict red).
  • channel_handle is always "@TheVerdict_USA".
  • Coordinates / image_path values may be placeholders that the
    producer fills later — for image_path use the string
    "PLACEHOLDER" and let the renderer draw the amber placeholder card.
  • For doc_screenshot.highlight use a sensible bbox (object with
    normalized x,y,w,h in 0-1) over the area of the document the
    quote would be in. If you cannot infer, use
    {{"x":0.1,"y":0.4,"w":0.8,"h":0.15}}.
  • For street_view, set location_label, event_label, and date_label
    from the case file (residence searched / courthouse beat).
  • For mugshot_card.charges, give up to 5 short labels — each ≤ 60
    chars so they fit the wrapped 2-line max.
  • For takeaway, footer is "FEDERAL CASES · NEW EVERY DAY".
  • For breaking_intro, ticker should be a single uppercase phrase
    ending in " · UNITED STATES" or similar.

CASE FILE (factual source — pull names, dates, locations from here):
{case_json}

VIEWER Q&A (additional factual material the narration weaves in):
{qa_block}

SCENES:
{scenes_json}

Return ONLY valid JSON of this shape (no markdown, no commentary):

{{
  "title": "<short title for this video>",
  "accent": "#dc2626",
  "source_case_id": "{case_id}",
  "channel_handle": "@TheVerdict_USA",
  "scenes": [
    {{
      "id": "s001",
      "narration": "...verbatim from the input...",
      "card": {{
        "layout": "breaking_intro",
        "agency": "...",
        "status_tag": "INDICTED",
        "headline": "...",
        "defendant_name": "...",
        "defendant_role": "...",
        "defendant_location": "...",
        "ticker": "..."
      }}
    }},
    ...
  ]
}}
"""


def write_script(project_dir: Path) -> Path:
    case_path = project_dir / "case_file.json"
    narr_path = project_dir / "narration.txt"
    if not case_path.is_file():
        raise SystemExit(f"no case_file.json at {case_path}")
    if not narr_path.is_file():
        raise SystemExit(
            f"no narration.txt at {narr_path}; run the narration step first"
        )
    case = _load_json(case_path)
    expansion = _ensure_expansion(project_dir, case)
    narration = narr_path.read_text()
    scenes = _parse_narration(narration)
    if not scenes:
        raise SystemExit("no scene tags found in narration.txt — expected "
                         "lines like `[s001 | breaking_intro]`")
    # Validate layouts
    bad = [s for s in scenes if s["layout"] not in LAYOUTS]
    if bad:
        names = ", ".join(f"{s['id']}={s['layout']}" for s in bad)
        raise SystemExit(f"unknown layout(s) in narration: {names}")
    if scenes[0]["layout"] != "breaking_intro":
        print(f"[scripter] WARNING: first scene is {scenes[0]['layout']}, "
              "expected breaking_intro")
    if scenes[-1]["layout"] != "takeaway":
        print(f"[scripter] WARNING: last scene is {scenes[-1]['layout']}, "
              "expected takeaway")
    if case.get("legal_flags", {}).get("mugshot_restricted_state"):
        mug_scenes = [s["id"] for s in scenes if s["layout"] == "mugshot_card"]
        if mug_scenes:
            print(f"[scripter] WARNING: mugshot_restricted_state is true but "
                  f"narration includes mugshot_card scenes: {mug_scenes}. "
                  "Edit narration.txt to use a different layout (e.g. "
                  "street_view or doc_screenshot) and re-run.")

    print(f"[scripter] parsed {len(scenes)} scenes from narration.txt")
    for s in scenes:
        print(f"           {s['id']}  {s['layout']:<16}  "
              f"{s['narration'][:60]}{'…' if len(s['narration'])>60 else ''}")

    import os
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)

    prompt = _SCRIPT_PROMPT.format(
        layout_guide=LAYOUT_GUIDE,
        case_json=_case_for_prompt(case),
        qa_block=_build_qa_block(expansion),
        scenes_json=json.dumps(scenes, indent=2),
        case_id=case.get("case_id", ""),
    )

    print(f"[scripter] building script.json ({config.MODEL_SCRIPTWRITING})")
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_SCRIPTWRITING,
                contents=prompt,
            )
            text = _strip_codefence(_text_of(resp))
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                raise ValueError("no JSON in script response")
            data = json.loads(m.group())
            break
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                print(f"  retry after {type(e).__name__}: {e}")
                time.sleep(3 * (attempt + 1))
            else:
                raise

    # Verbatim narration enforcement: stamp the parsed narration back
    # in case Gemini quietly rewrote anything.
    by_id = {s["id"]: s for s in scenes}
    out_scenes = data.get("scenes") or []
    fixed = 0
    for sc in out_scenes:
        src = by_id.get(sc.get("id"))
        if src and sc.get("narration", "").strip() != src["narration"].strip():
            sc["narration"] = src["narration"]
            fixed += 1
        # Force layout to match what the user approved.
        if src:
            sc.setdefault("card", {})["layout"] = src["layout"]
    if fixed:
        print(f"[scripter] restored {fixed} narration block(s) to verbatim text")

    # Ensure required globals
    data.setdefault("accent", "#dc2626")
    data.setdefault("channel_handle", "@TheVerdict_USA")
    data.setdefault("source_case_id", case.get("case_id"))
    if not data.get("title"):
        data["title"] = case.get("headline", "Federal Indictment")[:80]

    out = project_dir / "script.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"[scripter] wrote {out}")
    return out


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verdict scripter (narration + script)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("narration",
                       help="Generate narration.txt (review before next step)")
    a.add_argument("project_dir")

    b = sub.add_parser("script",
                       help="Generate script.json from approved narration.txt")
    b.add_argument("project_dir")

    args = ap.parse_args(argv)
    proj = Path(args.project_dir)
    if not proj.is_dir():
        raise SystemExit(f"not a directory: {proj}")

    if args.cmd == "narration":
        write_narration(proj)
    elif args.cmd == "script":
        write_script(proj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
