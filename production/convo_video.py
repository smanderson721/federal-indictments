#!/usr/bin/env python3
"""
production/convo_video.py — Convo → educational YouTube video.

Takes a saved Gemini conversation (from research_output/convos/<id>.json),
uses Gemini Pro to plan a one-sided educational video presenting only what
Gemini said in the conversation, narrates each scene with Gemini TTS, renders
each scene as a D3/Canvas animation (essay_card.html), and assembles the
final MP4.

No AI-generated photos / videos — pure animated cards.

Public entry point:
    plan_and_produce(convo_id: str, voice: str = "Enceladus") -> dict

Returns a summary dict with `slug`, `output_path`, `duration_sec`, etc.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from google import genai
from google.genai import types

import config
from production.d3_render import render_d3_scene
from production.gemini_tts import synth_one as gemini_tts


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

PLANNER_MODEL = "gemini-3.1-pro-preview"
DEFAULT_TTS_VOICE = "Enceladus"           # used elsewhere for narration
PROJECT_PREFIX = "convo"

# layout name → D3 template file under d3_scenes/.
# Everything not in this map falls through to essay_card.html (which renders
# title / section / bullets / quote / definition / number / takeaway layouts).
TEMPLATE_REGISTRY = {
    "world_map": "world_map.html",
    "timeline":  "historical_timeline.html",
    "chart":     "population_chart.html",
}
DEFAULT_TEMPLATE = "essay_card.html"

# Verdict (federal-crime YouTube Shorts) layouts all share one vertical
# template; the per-scene `card.layout` field switches between them
# inside vertical_indicted.html itself.
VERDICT_TEMPLATE = "vertical_indicted.html"
VERDICT_LAYOUTS = {
    "breaking_intro", "doc_screenshot", "mugshot_card",
    "street_view", "quote_card", "takeaway",
}
VERTICAL_RESOLUTION = (1080, 1920)

# Speech pacing — Gemini TTS at 1.0x is ~140 WPM; we don't speed-shift here.
WORDS_PER_SECOND = 2.3
MIN_SCENE_DUR = 4.0
MAX_SCENE_DUR = 24.0
TAIL_PAD = 0.4   # seconds of silence after narration ends
HEAD_PAD = 0.25  # seconds of silence before narration begins


# ─────────────────────────────────────────────────────────────────────────
# Convo loading
# ─────────────────────────────────────────────────────────────────────────

def _load_convo(convo_id: str) -> dict:
    from research.convo_manager import load_convo
    convo = load_convo(convo_id)
    if not convo:
        raise ValueError(f"convo not found: {convo_id}")
    if not convo.get("messages"):
        raise ValueError("convo is empty")
    return convo


def _gemini_text(convo: dict) -> str:
    """Concatenate only Gemini's (model role) replies into one body of text.
    The user's questions are dropped — the video is one-sided and educational."""
    blocks: list[str] = []
    for m in convo.get("messages", []):
        if m.get("role") in ("model", "assistant"):
            content = (m.get("content") or "").strip()
            if content:
                blocks.append(content)
    return "\n\n---\n\n".join(blocks)


def _slugify(text: str, max_len: int = 48) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", (text or "").lower())
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text or "convo")[:max_len].strip("-")


# ─────────────────────────────────────────────────────────────────────────
# Gemini Pro: plan the video
# ─────────────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are a video producer. You will receive the full text of what
a Gemini AI assistant said across one conversation. Your job is to write a
one-sided educational YouTube video that presents only the information from
that text. The user's original questions are NOT included and must NOT be
referenced or implied. The narration must be in a direct, declarative,
educator tone — no "the question was…", no "you asked…", no second-person
addresses to a single questioner. Treat the viewer as a general audience.

Plan the video as a sequence of scenes. Each scene has narration (1–4
sentences) and one animated visual. There are TWO families of visuals:
data-driven animations and typographic cards. Prefer the animated ones
whenever the content supports them. The video should feel visually varied —
do not use the same layout back-to-back if you can help it.

═════ DATA-DRIVEN ANIMATIONS (prefer these for history content) ═════

  layout = "world_map"
    An animated world map. Use whenever the content mentions geographic
    movement (migrations, invasions, colonization, trade routes, the spread
    of an idea or disease), the location of an empire/culture, or a list of
    historically significant places.
    Required: title (optional), subtitle (optional).
    Optional fields (all coordinates are [longitude, latitude] in decimal
    degrees; longitude west = negative, latitude south = negative):
      "camera":   { "center": [lon, lat], "scale": 800 }
                  scale ~250 = whole globe; ~800 = continental zoom;
                  ~1500 = country zoom. Choose based on coverage.
      "regions":  [ { "label": "Roman Empire", "center": [lon, lat],
                      "radius_km": 1400, "color": "#C04F4F",
                      "appear": 0.05, "fade": 0.95 } ]
                  Translucent circles for territorial extent.
      "arcs":     [ { "label": "Sea Peoples", "from": [lon, lat],
                      "to": [lon, lat], "color": "#FF6B35",
                      "start": 0.20, "end": 0.55, "curve": 0.30 } ]
                  Curved animated lines for migrations / invasions.
                  start/end are normalized to scene duration (0–1).
      "points":   [ { "label": "Carthage falls (146 BCE)",
                      "coord": [10.32, 36.85], "color": "#F0B86E",
                      "appear": 0.65 } ]
                  Event markers — labeled dots that pop in.
      "ticker":   { "start": -1300, "end": -1050, "label": "YEAR" }
                  Big year counter that sweeps through the era.
    Use 1–4 arcs OR 2–6 points OR 1–4 regions per scene. Not all at once.
    Always provide approximate lon/lat from your geographic knowledge.

  layout = "timeline"
    A horizontal animated chronology. Use when the content describes a
    sequence of dated events, periodization, or cause-and-effect across
    years/centuries.
    Required: "range": { "start": -1300, "end": -1050 } (negative = BCE).
    Optional:
      "title", "subtitle".
      "eras":   [ { "label": "Late Bronze", "start": -1300, "end": -1180,
                    "color": "#3B2D52" } ]
                Background color bands.
      "lanes":  ["Politics", "Technology", "Religion"]
                Horizontal swim lanes. Default = single lane.
      "events": [ { "year": -1207, "lane": "Politics",
                    "label": "Sea Peoples attack Egypt", "color": "#FF6B35" } ]
                4–10 events. Each event's lane must match an entry in
                "lanes" (or omit the lane field if you didn't define any).
      "cursor": true   // sweeping time cursor; default true.

  layout = "chart"
    An animated line chart drawn left-to-right. Use when the content
    describes quantities changing over time (population, casualties, GDP,
    territory in km², empire counts, life expectancy).
    Required: "series": [ { "name": "Italy", "color": "#FF6B35",
                            "fill": true,
                            "points": [[x, y], [x, y], ...] } ]
    Optional:
      "title", "subtitle".
      "x_label", "y_label".
      "x_is_year": true  // formats x ticks as BCE / CE; default true.
      "y_log": false     // log-scale y; default false.
      "annotations": [ { "x": -27, "y": 7.4, "label": "Augustus",
                         "color": "#F0B86E" } ]
                       Labeled dots on the chart.
    Each series should have 4–10 points. You can include 1–3 series.
    Only use this layout if real numeric data from the source supports it.

═════ TYPOGRAPHIC CARDS (use for connective scenes) ═════

  layout = "title"      — opening title. Fields: title, subtitle.
  layout = "section"    — chapter heading transition. Fields: heading, marker
                          (optional roman numeral or "01").
  layout = "bullets"    — 3–6 short bullets. Fields: heading,
                          bullets (array of ≤8-word strings).
  layout = "quote"      — single big sentence. Fields: quote, attribution.
  layout = "definition" — term + definition. Fields: term (1–4 words),
                          definition (1 sentence).
  layout = "number"     — a striking statistic. Fields: number (string),
                          caption (one short line).
  layout = "takeaway"   — pulled-out summary sentence. Fields: text.

═════ Rules ═════

- 8–24 scenes total.
- First scene MUST be "title". Last scene MUST be "takeaway".
- Aim for ~40–60% data-driven visuals (world_map / timeline / chart) when the
  topic supports them. History topics almost always do.
- Use "section" sparingly (2–4 chapter breaks for longer videos).
- Card text must be standalone (no "as mentioned earlier" phrasing).
- Narration is what the narrator SAYS OUT LOUD — write it for the ear.
  12–55 words per scene. Avoid filler ("Here's the thing", "Let me explain").
- The visual highlights the key idea; the narration elaborates with detail.
- Pick a sensible accent hex color for the whole video. Apply it as the
  default arc/marker color for maps and the default line color for charts
  unless a specific scene benefits from its own palette.

Return ONLY a JSON object with this exact shape (no markdown fences):

{
  "title": "Video title (≤9 words)",
  "accent": "#D2A24C",
  "scenes": [
    {
      "id": "s001",
      "narration": "Spoken sentence(s) for this scene.",
      "card": { "layout": "title", "title": "...", "subtitle": "..." }
    },
    {
      "id": "s002",
      "narration": "...",
      "card": {
        "layout": "world_map",
        "title": "Sea Peoples raids",
        "subtitle": "1200–1150 BCE",
        "camera": { "center": [30, 35], "scale": 900 },
        "arcs": [
          { "label": "from the Aegean",  "from": [25.0, 38.0],
            "to": [31.0, 31.0], "start": 0.25, "end": 0.65 },
          { "label": "into the Levant",  "from": [30.0, 36.0],
            "to": [35.5, 35.0], "start": 0.40, "end": 0.80 }
        ],
        "points": [
          { "label": "Ugarit destroyed", "coord": [35.78, 35.60], "appear": 0.70 }
        ],
        "ticker": { "start": -1250, "end": -1100, "label": "YEAR" }
      }
    }
  ]
}
"""


def _plan_video(gemini_text: str, client: genai.Client) -> dict:
    """Call Gemini Pro to plan the video. Returns the script dict."""
    prompt = PLANNER_SYSTEM + (
        "\n\nSOURCE TEXT (everything Gemini said in the conversation):\n\n"
        + gemini_text[:80_000]
    )
    resp = client.models.generate_content(
        model=PLANNER_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            response_mime_type="application/json",
        ),
    )
    raw = (resp.text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise RuntimeError(f"Planner did not return JSON. Got: {raw[:400]}")
        data = json.loads(m.group())

    if not isinstance(data, dict) or "scenes" not in data:
        raise RuntimeError(f"Planner JSON missing 'scenes': {raw[:400]}")

    # Normalize: assign ids if missing
    for i, s in enumerate(data["scenes"]):
        if not s.get("id"):
            s["id"] = f"s{i + 1:03d}"
        s["narration"] = (s.get("narration") or "").strip()
        if "card" not in s or not isinstance(s["card"], dict):
            s["card"] = {"layout": "takeaway", "text": s["narration"][:80]}
    data["title"] = data.get("title") or "Conversation"
    data["accent"] = data.get("accent") or "#58A6FF"
    return data


# ─────────────────────────────────────────────────────────────────────────
# Narration via Gemini TTS
# ─────────────────────────────────────────────────────────────────────────

def _ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds, or 0.0 on failure."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _pad_audio(in_wav: Path, out_wav: Path, head: float, tail: float) -> None:
    """Pad an audio file with silence at head and tail. Output is WAV."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(in_wav),
        "-af", f"adelay={int(head * 1000)}|{int(head * 1000)},apad=pad_dur={tail}",
        "-ar", "24000", "-ac", "1",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _narrate_scenes(script: dict, project_dir: Path, voice: str,
                    client: genai.Client, style: str = "chess") -> None:
    """Synthesize narration for every scene. Writes audio/<id>.wav and
    annotates each scene with `audio_path` and `duration` (incl. padding).

    ``style`` picks the director-notes preamble:
      • "chess" → British "Reginald" (legacy default, en-GB)
      • "news"  → American newsroom (en-US) — used by The Verdict
    """
    audio_dir = project_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Resolve preamble + language for the chosen style.
    if style == "news":
        from production.news_voice import NEWS_PREAMBLE, NEWS_LANGUAGE_CODE
        tts_kwargs = {"preamble": NEWS_PREAMBLE,
                      "language_code": NEWS_LANGUAGE_CODE}
    elif style == "chess":
        tts_kwargs = {}   # function defaults → Reginald + en-GB
    else:
        raise ValueError(f"unknown narration style: {style!r}")

    for scene in script["scenes"]:
        sid = scene["id"]
        text = scene["narration"]
        if not text:
            scene["duration"] = MIN_SCENE_DUR
            continue

        raw_wav = audio_dir / f"{sid}_raw.wav"
        final_wav = audio_dir / f"{sid}.wav"

        print(f"  [tts] {sid}: {text[:60]}...", flush=True)
        gemini_tts(client, voice, text, raw_wav, **tts_kwargs)
        raw_dur = _ffprobe_duration(raw_wav)
        if raw_dur <= 0:
            # Fallback: estimate by word count if probe fails
            raw_dur = max(MIN_SCENE_DUR,
                          len(text.split()) / WORDS_PER_SECOND)

        _pad_audio(raw_wav, final_wav, HEAD_PAD, TAIL_PAD)
        raw_wav.unlink(missing_ok=True)

        dur = max(MIN_SCENE_DUR,
                  min(MAX_SCENE_DUR, raw_dur + HEAD_PAD + TAIL_PAD))
        scene["audio_path"] = str(final_wav)
        scene["duration"] = dur


# ─────────────────────────────────────────────────────────────────────────
# D3 visual rendering
# ─────────────────────────────────────────────────────────────────────────

def _render_scene_visuals(script: dict, project_dir: Path,
                          vertical: bool = False) -> None:
    visuals_dir = project_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)

    n_scenes = len(script["scenes"])
    accent = script.get("accent", "#58A6FF")
    channel_handle = script.get("channel_handle")
    resolution = VERTICAL_RESOLUTION if vertical else None

    for idx, scene in enumerate(script["scenes"], 1):
        sid = scene["id"]
        out_path = visuals_dir / f"{sid}.mp4"

        # Compose D3 config from the card spec
        card = dict(scene.get("card") or {})
        layout = card.get("layout") or "takeaway"
        card.setdefault("layout", layout)
        card.setdefault("accent", accent)
        if channel_handle:
            card.setdefault("channel_handle", channel_handle)

        # Template selection: Verdict layouts all live in one vertical
        # template; everything else falls back to the horizontal essay
        # / map / chart / timeline templates.
        if vertical or layout in VERDICT_LAYOUTS:
            template = VERDICT_TEMPLATE
        else:
            template = TEMPLATE_REGISTRY.get(layout, DEFAULT_TEMPLATE)
            if template == DEFAULT_TEMPLATE:
                card.setdefault(
                    "footer",
                    f"{script.get('title', '')} · {idx}/{n_scenes}")

        d3_cfg = dict(card)

        print(f"  [d3]  {sid} layout={layout} template={template} "
              f"duration={scene['duration']:.1f}s", flush=True)
        ok = render_d3_scene(
            template=template,
            d3_config=d3_cfg,
            output_path=out_path,
            duration=scene["duration"],
            resolution=resolution,
        )
        if not ok:
            raise RuntimeError(f"D3 render failed for scene {sid}")
        scene["visual_path"] = str(out_path)


# ─────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────

def _mux_scene(visual_mp4: Path, audio_wav: Path, out_mp4: Path,
               duration: float) -> None:
    """Mux a silent D3 video with a narration WAV. Truncates audio to
    visual duration if needed (audio is shorter due to padding rules)."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(visual_mp4),
        "-i", str(audio_wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-t", f"{duration:.3f}",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat_clips(clips: list[Path], out_mp4: Path) -> None:
    list_path = out_mp4.parent / "_concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for c in clips:
            esc = str(c.resolve()).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    list_path.unlink(missing_ok=True)


def _assemble(script: dict, project_dir: Path) -> Path:
    tmp_dir = project_dir / "tmp_clips"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    for i, scene in enumerate(script["scenes"], 1):
        sid = scene["id"]
        clip = tmp_dir / f"{i:03d}_{sid}.mp4"
        visual = Path(scene["visual_path"])
        audio = Path(scene.get("audio_path", ""))
        if audio.exists():
            _mux_scene(visual, audio, clip, scene["duration"])
        else:
            # No narration — copy visual through with silent track
            silent = tmp_dir / f"{sid}_silent.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "anullsrc=cl=mono:r=24000",
                 "-t", f"{scene['duration']:.3f}",
                 str(silent)],
                check=True, capture_output=True,
            )
            _mux_scene(visual, silent, clip, scene["duration"])
            silent.unlink(missing_ok=True)
        clips.append(clip)

    slug = project_dir.name
    output = project_dir / f"{slug}.mp4"
    _concat_clips(clips, output)

    # Cleanup
    for c in clips:
        c.unlink(missing_ok=True)
    try:
        tmp_dir.rmdir()
    except OSError:
        pass
    return output


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def produce_from_script(project_dir: str | Path,
                        voice: str = DEFAULT_TTS_VOICE) -> dict:
    """Produce a video from a hand-authored ``script.json`` already sitting
    in ``project_dir``. Skips the Gemini Pro planner — used when Copilot
    writes the script directly (history pipeline).

    The script.json must have shape::

        {
          "title": "...",
          "accent": "#D2A24C",
          "scenes": [ { "id": "s001", "narration": "...",
                        "card": { "layout": "...", ... } }, ... ]
        }
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set (needed for Gemini TTS)")
    project_dir = Path(project_dir)
    script_path = project_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json not found in {project_dir}")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    if not isinstance(script, dict) or "scenes" not in script:
        raise RuntimeError("script.json missing 'scenes' array")

    # Normalize ids / accent / title
    for i, s in enumerate(script["scenes"]):
        if not s.get("id"):
            s["id"] = f"s{i + 1:03d}"
        s["narration"] = (s.get("narration") or "").strip()
        if "card" not in s or not isinstance(s["card"], dict):
            s["card"] = {"layout": "takeaway", "text": s["narration"][:80]}
    script.setdefault("title", project_dir.name)
    script.setdefault("accent", "#D2A24C")

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    print(f"\n{'=' * 60}")
    print(f"  PRODUCE FROM SCRIPT: {script.get('title')}")
    print(f"  project: {project_dir}")
    print(f"  scenes:  {len(script['scenes'])}")
    print(f"{'=' * 60}\n")

    # 1. Narrate
    print("Synthesizing narration (Gemini TTS)...", flush=True)
    _narrate_scenes(script, project_dir, voice, client)

    # 2. Render visuals
    print("\nRendering D3 animations...", flush=True)
    _render_scene_visuals(script, project_dir)

    # Re-save with durations + paths
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    # 3. Assemble
    print("\nAssembling final video...", flush=True)
    output = _assemble(script, project_dir)
    duration = _ffprobe_duration(output)

    print(f"\n{'=' * 60}")
    print(f"  ✓ VIDEO COMPLETE: {output}")
    print(f"  duration: {duration:.1f}s ({duration / 60:.1f} min)")
    print(f"{'=' * 60}\n")

    return {
        "slug": project_dir.name,
        "title": script.get("title"),
        "output_path": str(output),
        "duration_sec": duration,
        "scene_count": len(script["scenes"]),
        "project_dir": str(project_dir),
    }


# ─────────────────────────────────────────────────────────────────────────
# Verdict (vertical YouTube Shorts) entry point
# ─────────────────────────────────────────────────────────────────────────

def produce_verdict_video(project_dir: str | Path,
                          voice: str | None = None) -> dict:
    """Produce a 1080x1920 vertical YouTube Short for The Verdict from a
    hand-authored ``script.json`` in ``project_dir``.

    Differences from ``produce_from_script``:
      • Renders at 1080x1920 instead of 1920x1080.
      • Routes every scene through ``vertical_indicted.html`` regardless
        of the ``layout`` field (the template's internal layout dispatch
        handles ``breaking_intro``, ``doc_screenshot``, ``mugshot_card``,
        ``street_view``, ``quote_card``, ``takeaway``).
      • Narrates with the American newsroom preamble (en-US) instead of
        British Reginald (en-GB).
      • Default voice is the one stored in
        ``research_output/channel_config.json`` under
        ``channels.verdict.voice`` (currently Aoede).
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set (needed for Gemini TTS)")
    project_dir = Path(project_dir)
    script_path = project_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json not found in {project_dir}")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    if not isinstance(script, dict) or "scenes" not in script:
        raise RuntimeError("script.json missing 'scenes' array")

    # Resolve voice: explicit > channel_config > Aoede fallback
    if voice is None:
        try:
            from production.voices import get_selected_voice
            voice = get_selected_voice("verdict") or "Aoede"
        except Exception:
            voice = "Aoede"

    # Normalize ids / accent / handle / cards
    for i, s in enumerate(script["scenes"]):
        if not s.get("id"):
            s["id"] = f"s{i + 1:03d}"
        s["narration"] = (s.get("narration") or "").strip()
        if "card" not in s or not isinstance(s["card"], dict):
            s["card"] = {"layout": "takeaway", "text": s["narration"][:80]}
    script.setdefault("title", project_dir.name)
    script.setdefault("accent", "#dc2626")
    script.setdefault("channel_handle", "@TheVerdict_USA")

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    print(f"\n{'=' * 60}")
    print(f"  PRODUCE VERDICT VIDEO: {script.get('title')}")
    print(f"  project: {project_dir}")
    print(f"  scenes:  {len(script['scenes'])}")
    print(f"  voice:   {voice} (American newsroom)")
    print(f"  format:  {VERTICAL_RESOLUTION[0]}x{VERTICAL_RESOLUTION[1]} vertical")
    print(f"{'=' * 60}\n")

    # 1. Narrate (Aoede + American newsroom preamble)
    print("Synthesizing narration (Gemini TTS, en-US)...", flush=True)
    _narrate_scenes(script, project_dir, voice, client, style="news")

    # 2. Resolve images (mugshots, court docs, street view) before rendering.
    #    Fills card.image_path for every layout that needs imagery; cached
    #    on disk so re-runs skip already-downloaded assets.
    print("\nResolving scene images (mugshot / doc / street view)...",
          flush=True)
    try:
        from production.indicted.image_resolver import resolve_images
        resolve_images(project_dir)
        # Reload only card.image_path fields from disk (resolve_images
        # writes them back to script.json). Don't overwrite the whole
        # in-memory script — that would clobber `duration` and
        # `audio_path` set by _narrate_scenes.
        with open(script_path, "r", encoding="utf-8") as f:
            disk_script = json.load(f)
        disk_by_id = {s.get("id"): s for s in disk_script.get("scenes", [])}
        for s in script["scenes"]:
            d = disk_by_id.get(s["id"])
            if not d:
                continue
            d_card = d.get("card") or {}
            if "image_path" in d_card:
                s.setdefault("card", {})["image_path"] = d_card["image_path"]
            if "image_paths" in d_card:
                s.setdefault("card", {})["image_paths"] = d_card["image_paths"]
    except Exception as e:
        print(f"  ⚠ image resolver failed: {e} — proceeding with placeholders",
              flush=True)

    # 3. Render visuals (vertical 1080x1920, vertical_indicted.html)
    print("\nRendering D3 vertical animations...", flush=True)
    _render_scene_visuals(script, project_dir, vertical=True)

    # Re-save with durations + paths
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    # 3. Assemble
    print("\nAssembling final Short...", flush=True)
    output = _assemble(script, project_dir)
    duration = _ffprobe_duration(output)

    print(f"\n{'=' * 60}")
    print(f"  ✓ VERDICT SHORT COMPLETE: {output}")
    print(f"  duration: {duration:.1f}s")
    print(f"{'=' * 60}\n")

    return {
        "slug": project_dir.name,
        "title": script.get("title"),
        "output_path": str(output),
        "duration_sec": duration,
        "scene_count": len(script["scenes"]),
        "project_dir": str(project_dir),
        "voice": voice,
    }


def plan_and_produce(convo_id: str,
                     voice: str = DEFAULT_TTS_VOICE) -> dict:
    """Plan + produce a video from a saved convo. Returns a summary dict."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    convo = _load_convo(convo_id)
    title_hint = convo.get("title") or convo_id

    client = genai.Client(api_key=config.GEMINI_API_KEY)

    print(f"\n{'=' * 60}")
    print(f"  CONVO → VIDEO: {title_hint}")
    print(f"  convo_id: {convo_id}")
    print(f"{'=' * 60}\n")

    # 1. Extract Gemini's text
    text = _gemini_text(convo)
    if not text.strip():
        raise RuntimeError("convo has no model replies to use")

    # 2. Plan the video
    print("Planning video with Gemini Pro...", flush=True)
    t0 = time.time()
    script = _plan_video(text, client)
    print(f"  planned {len(script['scenes'])} scenes "
          f"({time.time() - t0:.1f}s)\n", flush=True)

    # 3. Make the project directory
    slug_base = _slugify(script.get("title") or title_hint)
    slug = f"{PROJECT_PREFIX}-{slug_base}-{int(time.time())}"
    project_dir = Path(config.project_dir(slug))
    project_dir.mkdir(parents=True, exist_ok=True)

    # Save the planning artifact
    script_path = project_dir / "script.json"
    script["source_convo_id"] = convo_id
    script["source_convo_title"] = title_hint
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    # 4. Narrate
    print("Synthesizing narration (Gemini TTS)...", flush=True)
    _narrate_scenes(script, project_dir, voice, client)

    # 5. Render visuals
    print("\nRendering D3 animations...", flush=True)
    _render_scene_visuals(script, project_dir)

    # Re-save the script with durations + paths
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    # 6. Assemble
    print("\nAssembling final video...", flush=True)
    output = _assemble(script, project_dir)
    duration = _ffprobe_duration(output)

    print(f"\n{'=' * 60}")
    print(f"  ✓ VIDEO COMPLETE: {output}")
    print(f"  duration: {duration:.1f}s ({duration / 60:.1f} min)")
    print(f"{'=' * 60}\n")

    return {
        "slug": slug,
        "title": script.get("title"),
        "output_path": str(output),
        "duration_sec": duration,
        "scene_count": len(script["scenes"]),
        "project_dir": str(project_dir),
        "source_convo_id": convo_id,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Make a video from a saved convo")
    p.add_argument("convo_id")
    p.add_argument("--voice", default=DEFAULT_TTS_VOICE)
    args = p.parse_args()
    summary = plan_and_produce(args.convo_id, voice=args.voice)
    print(json.dumps(summary, indent=2))
