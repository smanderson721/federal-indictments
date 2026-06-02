"""
Gemini TTS voice registry + sample-cache helper.

The Voices tab in pitches.html lets the user audition every Gemini TTS voice
with arbitrary text and pick a favorite per channel. This module:

  * Defines `GEMINI_TTS_VOICES` — the full list of 30 prebuilt voices.
  * Provides `synth_sample(voice, text)` — synthesizes one clip via
    Gemini TTS, caches it on disk keyed by (voice, text-hash), and
    returns the file path. Repeat calls with the same text return
    the cached WAV instantly.
  * Provides `get_selected_voice(channel)` / `set_selected_voice(channel, voice)`
    backed by `research_output/channel_config.json`. The crime pipeline
    (channel="indicted") reads its narration voice from here.

Voice metadata (tag) comes from Google's published voice descriptions —
useful for grouping the UI by tone (Bright, Firm, Calm, etc.).
"""

from __future__ import annotations

import hashlib
import json
import os
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_ROOT / "research_output" / "voice_samples"
CONFIG_PATH = PROJECT_ROOT / "research_output" / "channel_config.json"


# Full list of Gemini 2.5 prebuilt TTS voices, with the tone descriptor
# Google publishes for each. Order matches Google's documentation.
GEMINI_TTS_VOICES: list[dict] = [
    {"name": "Zephyr",         "tag": "Bright",         "gender": "female"},
    {"name": "Puck",           "tag": "Upbeat",         "gender": "male"},
    {"name": "Charon",         "tag": "Informative",    "gender": "male"},
    {"name": "Kore",           "tag": "Firm",           "gender": "female"},
    {"name": "Fenrir",         "tag": "Excitable",      "gender": "male"},
    {"name": "Leda",           "tag": "Youthful",       "gender": "female"},
    {"name": "Orus",           "tag": "Firm",           "gender": "male"},
    {"name": "Aoede",          "tag": "Breezy",         "gender": "female"},
    {"name": "Callirrhoe",     "tag": "Easy-going",     "gender": "female"},
    {"name": "Autonoe",        "tag": "Bright",         "gender": "female"},
    {"name": "Enceladus",      "tag": "Breathy",        "gender": "male"},
    {"name": "Iapetus",        "tag": "Clear",          "gender": "male"},
    {"name": "Umbriel",        "tag": "Easy-going",     "gender": "male"},
    {"name": "Algieba",        "tag": "Smooth",         "gender": "male"},
    {"name": "Despina",        "tag": "Smooth",         "gender": "female"},
    {"name": "Erinome",        "tag": "Clear",          "gender": "female"},
    {"name": "Algenib",        "tag": "Gravelly",       "gender": "male"},
    {"name": "Rasalgethi",     "tag": "Informative",    "gender": "male"},
    {"name": "Laomedeia",      "tag": "Upbeat",         "gender": "female"},
    {"name": "Achernar",       "tag": "Soft",           "gender": "female"},
    {"name": "Alnilam",        "tag": "Firm",           "gender": "male"},
    {"name": "Schedar",        "tag": "Even",           "gender": "male"},
    {"name": "Gacrux",         "tag": "Mature",         "gender": "female"},
    {"name": "Pulcherrima",    "tag": "Forward",        "gender": "female"},
    {"name": "Achird",         "tag": "Friendly",       "gender": "male"},
    {"name": "Zubenelgenubi",  "tag": "Casual",         "gender": "male"},
    {"name": "Vindemiatrix",   "tag": "Gentle",         "gender": "female"},
    {"name": "Sadachbia",      "tag": "Lively",         "gender": "male"},
    {"name": "Sadaltager",     "tag": "Knowledgeable",  "gender": "male"},
    {"name": "Sulafat",        "tag": "Warm",           "gender": "female"},
]

VOICE_NAMES: set[str] = {v["name"] for v in GEMINI_TTS_VOICES}

# Sample prompt the Voices tab pre-fills — chosen to mirror the
# tone of an Indicted Shorts narration.
DEFAULT_SAMPLE_TEXT = (
    "Federal prosecutors in the Southern District of New York today unsealed "
    "a forty-seven count indictment against the defendant, charging him with "
    "wire fraud, money laundering, and conspiracy. If convicted, he faces a "
    "maximum sentence of life in prison."
)


def _sample_path(voice: str, text: str, style: str = "news") -> Path:
    """Deterministic cache path for a (voice, style, text) triple.

    `style` selects which director-notes preamble is applied during TTS
    (e.g. "news" → American newsroom). It's part of the cache key so a
    voice cached under one style isn't returned for another.
    """
    h = hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]
    return SAMPLES_DIR / f"{voice}__{style}__{h}.wav"


def existing_sample(voice: str, text: str, style: str = "news") -> Path | None:
    p = _sample_path(voice, text, style)
    return p if p.is_file() else None


def synth_sample(voice: str, text: str, force: bool = False,
                 style: str = "news") -> Path:
    """Synthesize one TTS clip and cache it. Returns the WAV path.

    `style` picks the director-notes preamble injected before the text:
      • "news"      → American newsroom (en-US, broadcast neutral)
      • "chess"     → British "Reginald" (en-GB, RP) — used by the chess
                      live commentary pipeline; do NOT use for any other
                      channel.

    Default is "news" because the Voices tab is currently the audition
    surface for The Verdict (the only non-chess production channel).

    If a cached file already exists for this (voice, style, text) tuple
    and `force` is False, returns it without making an API call.
    """
    if voice not in VOICE_NAMES:
        raise ValueError(f"Unknown Gemini TTS voice: {voice!r}")
    out = _sample_path(voice, text, style)
    if out.is_file() and not force:
        return out
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    # When force=True, the user explicitly asked to replace the cached
    # sample for this voice — clear any other cached samples for this
    # (voice, style) pair so the UI displays only the freshly generated
    # clip. Different (voice) or (style) caches are left alone.
    if force and SAMPLES_DIR.is_dir():
        prefix = f"{voice}__{style}__"
        for p in SAMPLES_DIR.iterdir():
            if p.name.startswith(prefix) and p.name.endswith(".wav") and p != out:
                try:
                    p.unlink()
                except OSError:
                    pass

    # Import lazily so the registry can be read without google-genai installed.
    from google import genai
    from production.live.build_commentary_gemini import synth_one

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")
    client = genai.Client(api_key=api_key)

    # Select preamble + language for the chosen style. Chess keeps the
    # function defaults (Reginald + en-GB); news swaps to the American
    # newsroom preamble defined in production.news_voice.
    if style == "news":
        from production.news_voice import NEWS_PREAMBLE, NEWS_LANGUAGE_CODE
        synth_one(client, voice, text, out,
                  preamble=NEWS_PREAMBLE, language_code=NEWS_LANGUAGE_CODE)
    elif style == "chess":
        synth_one(client, voice, text, out)  # Reginald defaults
    else:
        raise ValueError(f"Unknown TTS style: {style!r}")
    return out


# ── Per-channel voice selection ─────────────────────────────────────────

def _load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_selected_voice(channel: str) -> str | None:
    cfg = _load_config()
    return ((cfg.get("channels") or {}).get(channel) or {}).get("voice")


def set_selected_voice(channel: str, voice: str) -> None:
    if voice not in VOICE_NAMES:
        raise ValueError(f"Unknown Gemini TTS voice: {voice!r}")
    cfg = _load_config()
    cfg.setdefault("channels", {}).setdefault(channel, {})["voice"] = voice
    _save_config(cfg)


def list_cached(voice: str | None = None,
                style: str | None = "news") -> list[dict]:
    """List cached WAV samples, optionally filtered by voice and style.

    Filename format: `<voice>__<style>__<hash>.wav`. Older files with the
    legacy `<voice>__<hash>.wav` format (British Reginald-flavored
    samples generated before the style split) are ignored by default —
    pass `style=None` to include them.
    """
    if not SAMPLES_DIR.is_dir():
        return []
    out = []
    for p in SAMPLES_DIR.iterdir():
        if not p.name.endswith(".wav"):
            continue
        stem = p.name[:-4]
        parts = stem.split("__")
        if len(parts) == 3:
            v, s, _h = parts
        elif len(parts) == 2:
            # Legacy file from before the style split — treat as "legacy".
            v, s = parts[0], "legacy"
        else:
            continue
        if voice and v != voice:
            continue
        if style is not None and s != style:
            continue
        # Read duration cheaply from WAV header.
        dur = 0.0
        try:
            with wave.open(str(p), "rb") as wf:
                dur = wf.getnframes() / float(wf.getframerate() or 1)
        except Exception:
            pass
        out.append({
            "voice": v,
            "style": s,
            "file": p.name,
            "bytes": p.stat().st_size,
            "duration": round(dur, 2),
            "mtime": int(p.stat().st_mtime),
        })
    out.sort(key=lambda r: (r["voice"], -r["mtime"]))
    return out
