"""Two-round factual-backstory expander for The Verdict.

Flow (per case):
    Round 1 — GENERAL INTERROGATIVE
        Prompt Gemini Pro (with Google Search grounding) as a viewer
        who simply wants to know MORE about what actually happened in
        this case.  The model produces a factual narrative of the
        events that led to the indictment — the conduct, the
        investigation, who was targeted, how it was uncovered.

    Round 2 — VIEWER FOLLOW-UP QUESTION
        Show Gemini Pro the original primer + the Round 1 answer.  It
        generates ONE follow-up question a viewer would naturally ask
        next.  The question MUST be about the backstory (motive,
        targets, methods, investigation) — NOT sentencing, defense
        strategy, or legal procedure.

    Round 3 — FOLLOW-UP ANSWER
        Gemini Flash-Lite (with Google Search grounding) answers the
        follow-up factually.

Two substantive Q&A blocks; both are meant to be woven into the
narration so the viewer leaves informed about the events of the case.

Models:
    • config.MODEL_RESEARCH (gemini-3.1-pro-preview)  — rounds 1 & 2
    • config.MODEL_SCORING  (gemini-3.1-flash-lite)   — round 3

Output schema (projects/<slug>/research_expansion.json):
    {
      "case_id":  "<id>",
      "primer":   "<text shown to Gemini>",
      "viewer_qa": [
        { "question": "...", "answer": "...", "sources": [...] },  # r1
        { "question": "...", "answer": "...", "sources": [...] }   # r3
      ]
    }
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import config


# ── Primer builder ────────────────────────────────────────────────────

def _build_primer(case: dict) -> str:
    lines: list[str] = []
    if case.get("headline"):
        lines.append(f"HEADLINE: {case['headline']}")
    if case.get("agency"):
        lines.append(f"AGENCY:   {case['agency'].upper()}")
    if case.get("filed_on"):
        lines.append(f"FILED:    {case['filed_on']}")

    defs = case.get("defendants") or []
    if defs:
        labels = []
        for d in defs:
            bits = [d.get("name") or "Unknown"]
            if d.get("role"):
                bits.append(f"({d['role']})")
            if d.get("age"):
                bits.append(f"age {d['age']}")
            city = d.get("city", "") or ""
            state = d.get("state", "") or ""
            if city or state:
                sep = ", " if city and state else ""
                bits.append(f"of {city}{sep}{state}")
            labels.append(" ".join(b for b in bits if b))
        lines.append("DEFENDANT(S): " + "; ".join(labels))

    charges = case.get("charges") or []
    if charges:
        cl = [c.get("count_name") or "" for c in charges if c.get("count_name")]
        if cl:
            lines.append("CHARGES: " + " | ".join(cl))

    if case.get("narrator_brief"):
        lines.append("\nBRIEF:\n" + case["narrator_brief"].strip())
    elif case.get("press_release_text"):
        lines.append("\nPRESS RELEASE EXCERPT:\n"
                     + case["press_release_text"][:2000].strip())

    return "\n".join(lines).strip()


# ── Internal helpers ─────────────────────────────────────────────────

def _strip_codefence(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _collect_sources(resp) -> list[str]:
    sources: list[str] = []
    try:
        gm = resp.candidates[0].grounding_metadata
        if gm and getattr(gm, "grounding_chunks", None):
            for ch in gm.grounding_chunks:
                if hasattr(ch, "web") and ch.web and ch.web.uri:
                    sources.append(ch.web.uri)
    except Exception:
        pass
    return sources


def _text_of(resp) -> str:
    return "".join(
        p.text for p in resp.candidates[0].content.parts
        if getattr(p, "text", None)
    ).strip()


# ── Round 1: general interrogative ───────────────────────────────────

ROUND1_QUESTION = (
    "Tell me more about what actually happened in this case — what was "
    "the underlying conduct, who was involved, what were the targets or "
    "victims, and how was it uncovered?"
)

_ROUND1_PROMPT = """\
You are answering a viewer's question about a federal criminal case
for a 60-second vertical news short. The viewer has heard a one-line
summary and now wants to know MORE about what actually happened.

Be FACTUAL, NEUTRAL, and SPECIFIC. Use Google Search to find concrete
details. Focus entirely on the backstory of the alleged conduct:
  • what the defendant(s) actually did, step by step
  • who they were targeting or working with
  • how the scheme operated
  • how investigators uncovered it
  • when key events happened

Do NOT discuss sentencing ranges, what the defense might argue, or
legal procedure. Stick to the WHAT and HOW of the events themselves.

Length: 4-6 sentences. Plain text, no markdown. Use 'allegedly'
framing while the case is pre-conviction. Do not begin with filler.

CASE PRIMER:
{primer}

VIEWER QUESTION:
{question}
"""


def _round1_general(client, primer: str) -> dict:
    from google.genai import types

    prompt = _ROUND1_PROMPT.format(primer=primer, question=ROUND1_QUESTION)
    search_tool = types.Tool(google_search=types.GoogleSearch())

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_RESEARCH,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[search_tool]),
            )
            return {
                "question": ROUND1_QUESTION,
                "answer": _text_of(resp),
                "sources": _collect_sources(resp),
            }
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"  ⚠ round 1 failed: {type(e).__name__}: {e}")
    return {"question": ROUND1_QUESTION, "answer": "", "sources": []}


# ── Round 2: generate ONE follow-up question ─────────────────────────

_ROUND2_PROMPT = """\
You are editing a 60-second vertical news short about a federal
criminal case. A viewer just heard the primer below, then heard a
factual narrative of what happened (the ROUND 1 ANSWER).

Your job: write ONE follow-up question that a curious viewer would
naturally ask next, triggered by something specific that came up in
the Round 1 answer.

Rules for the follow-up question:
  • It MUST be about the factual backstory — motive, methods, targets,
    relationships, the investigation, the broader context of what was
    happening. NOT about sentencing, plea bargains, defense strategy,
    legal procedure, or hypothetical "what if" outcomes.
  • It MUST be answerable from public reporting — a real fact-seeking
    question, not opinion or speculation.
  • It MUST go DEEPER than Round 1, not restate it. Pick a single
    detail from Round 1 that begs follow-up and probe that.
  • Phrase it the way a viewer would phrase it — short, direct, one
    sentence. Examples of the right shape:
        "Why were the Iranian hackers targeting this person specifically?"
        "How did the FBI first get tipped off?"
        "What was actually on the documents he kept at home?"
  • No multi-part questions. No preamble. Just the question.

Return ONLY valid JSON of the form:
{{"question": "..."}}

CASE PRIMER:
{primer}

ROUND 1 ANSWER:
{round1_answer}
"""


def _round2_followup_question(client, primer: str, round1_answer: str) -> str:
    prompt = _ROUND2_PROMPT.format(primer=primer, round1_answer=round1_answer)
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_RESEARCH,
                contents=prompt,
            )
            text = _strip_codefence(_text_of(resp))
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                raise ValueError("no JSON in follow-up response")
            data = json.loads(m.group())
            q = (data.get("question") or "").strip()
            if not q:
                raise ValueError("empty follow-up question")
            return q
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"  ⚠ follow-up question generation failed: "
                      f"{type(e).__name__}: {e}")
    return ""


# ── Round 3: answer the follow-up ────────────────────────────────────

_ROUND3_PROMPT = """\
You are answering a viewer's follow-up question about a federal
criminal case for a 60-second vertical news short.

Be FACTUAL, NEUTRAL, and SPECIFIC. Use Google Search to ground every
factual claim. Focus on the WHAT and WHY of the actual events — do
NOT pivot to sentencing, defense strategy, or procedure.

Length: 3-5 sentences. Plain text, no markdown. Use 'allegedly'
framing while the case is pre-conviction. Do not begin with filler
("Great question", "That's an interesting question"). Do not restate
the question. Do not repeat material already covered in ROUND 1 —
add NEW factual detail.

CASE PRIMER:
{primer}

ROUND 1 (background already covered):
{round1_answer}

FOLLOW-UP QUESTION:
{question}
"""


def _round3_answer(client, primer: str, round1_answer: str,
                   question: str) -> dict:
    from google.genai import types

    prompt = _ROUND3_PROMPT.format(
        primer=primer, round1_answer=round1_answer, question=question,
    )
    search_tool = types.Tool(google_search=types.GoogleSearch())

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=config.MODEL_SCORING,
                contents=prompt,
                config=types.GenerateContentConfig(tools=[search_tool]),
            )
            return {
                "question": question,
                "answer": _text_of(resp),
                "sources": _collect_sources(resp),
            }
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"  ⚠ round 3 failed: {type(e).__name__}: {e}")
    return {"question": question, "answer": "", "sources": []}


# ── Public entry point ───────────────────────────────────────────────

def expand_case_research(case: dict, project_dir: Path | None = None) -> dict:
    """Run the two-round backstory expansion on a parsed case_file dict.

    Returns the expansion dict; also writes
    ``<project_dir>/research_expansion.json`` if project_dir is given.
    """
    import os
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)

    primer = _build_primer(case)
    print(f"[expander] primer built ({len(primer)} chars)")

    # Round 1
    print(f"[expander] round 1 — general 'tell me what happened' "
          f"({config.MODEL_RESEARCH} + grounding)")
    r1 = _round1_general(client, primer)
    preview1 = (r1["answer"] or "").replace("\n", " ")[:160]
    print(f"          A1: {preview1}{'…' if len(r1['answer']) > 160 else ''}")
    print(f"          sources: {len(r1['sources'])}")

    qa: list[dict] = [r1]

    if r1["answer"]:
        # Round 2
        print(f"[expander] round 2 — generating follow-up question "
              f"({config.MODEL_RESEARCH})")
        followup = _round2_followup_question(client, primer, r1["answer"])
        if followup:
            print(f"          Q2: {followup}")
            time.sleep(1.0)
            # Round 3
            print(f"[expander] round 3 — answering follow-up "
                  f"({config.MODEL_SCORING} + grounding)")
            r3 = _round3_answer(client, primer, r1["answer"], followup)
            preview3 = (r3["answer"] or "").replace("\n", " ")[:160]
            print(f"          A3: {preview3}{'…' if len(r3['answer']) > 160 else ''}")
            print(f"          sources: {len(r3['sources'])}")
            qa.append(r3)
        else:
            print("[expander] no follow-up question produced; "
                  "returning round 1 only")

    result = {
        "case_id": case.get("case_id"),
        "primer": primer,
        "viewer_qa": qa,
    }

    if project_dir is not None:
        out = Path(project_dir) / "research_expansion.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2))
        print(f"[expander] wrote {out}")

    return result


# ── CLI: python -m research.indicted.expander <project_dir> ──────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Two-round backstory expander for The Verdict")
    ap.add_argument("project_dir",
                    help="path to projects/crime-<slug>/ "
                         "containing case_file.json")
    args = ap.parse_args()

    proj = Path(args.project_dir)
    cf = proj / "case_file.json"
    if not cf.is_file():
        raise SystemExit(f"no case_file.json at {cf}")
    case = json.loads(cf.read_text())
    expand_case_research(case, project_dir=proj)
