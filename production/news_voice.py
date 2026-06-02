"""
American-news TTS preamble + helpers.

Used by:
  • The Voices tab in pitches.html (when auditioning voices for The
    Verdict / federal-crime news channel).
  • The Verdict producer pipeline (`research/indicted/...` → narration).

This module deliberately does NOT touch the chess Reginald preamble in
`production/live/build_commentary_gemini.py` — that path keeps its
British defaults. Anything that wants an American newsroom voice imports
from here and passes `preamble=NEWS_PREAMBLE, language_code="en-US"`
into `synth_one()` / `synth_one_async()`.
"""

from __future__ import annotations

from pathlib import Path

# Director's-notes preamble that shapes the model's accent + delivery.
# Mirrors the structure of the Reginald preamble but flips every cue
# toward an American newsroom register.
NEWS_PREAMBLE = """\
You are synthesizing speech for a U.S. federal-crime news short.

# AUDIO PROFILE: The Verdict — Anchor Voice
## "American Newsroom"

## THE SCENE: A National Network Newsroom
The anchor is reading a short, hard-news federal-court update directly
into camera. The setting is a contemporary American cable-news studio:
LED panels, anchor desk, teleprompter. The tone is the tone of a network
evening-news lead — measured, authoritative, plain, factual. The story is
about a real federal indictment, conviction, sentencing, or arrest.

### DIRECTOR'S NOTES
Style: Calm, authoritative, neutral. Reads like a top-of-the-hour news
update on a major U.S. broadcast network — CBS Evening News, NBC Nightly
News, NPR's All Things Considered. No salesmanship, no hype, no theatrical
breathiness, no editorializing. Treat every sentence as a statement of
fact being read off a teleprompter to a national audience.

Pacing: Steady news-anchor cadence, roughly 150-170 WPM. Crisp diction.
Brief pauses at commas and periods. Numbers, dates, and statute names are
spoken cleanly and unhurriedly. Never trail off. Never improvise.

Accent: STANDARD AMERICAN ENGLISH (General American). The neutral
broadcast English of a network news anchor based in New York or
Washington, D.C. Rhotic — clearly pronounced 'r' at the ends of words
("reporter", "court", "murder"). Flat 'a' in words like "trap", "ask",
"man". NOT British. NOT Received Pronunciation. NOT Mid-Atlantic. NOT
Southern. NOT regional. Every clip in this channel must sound like the
same American anchor.
Language: en-US.

### COPY
"""

# Sample text the Voices tab pre-fills when auditioning a voice for the
# news channel — short, hard-news, deliberately the kind of phrasing the
# producer will actually generate.
DEFAULT_NEWS_SAMPLE_TEXT = (
    "Federal prosecutors in the Southern District of New York today "
    "unsealed a forty-seven count indictment against the defendant, "
    "charging him with wire fraud, money laundering, and conspiracy. "
    "If convicted, he faces a maximum sentence of life in prison."
)

NEWS_LANGUAGE_CODE = "en-US"


def synth_news_sample(voice: str, text: str, out_wav: Path, force: bool = False) -> Path:
    """Synthesize one American-news TTS clip to `out_wav`.

    Thin wrapper around `synth_one()` that injects the news preamble +
    en-US language code. Mirrors the signature of
    `voices.synth_sample()` but writes to an explicit path.
    """
    import os
    from google import genai
    from production.live.build_commentary_gemini import synth_one

    if out_wav.is_file() and not force:
        return out_wav
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")
    client = genai.Client(api_key=api_key)
    synth_one(
        client, voice, text, out_wav,
        preamble=NEWS_PREAMBLE,
        language_code=NEWS_LANGUAGE_CODE,
    )
    return out_wav
