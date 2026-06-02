"""Gemini TTS helper for The Verdict.

Carved out of the original `production/live/build_commentary_gemini.py`
(chess-livestream context) into a minimal standalone module: just
`synth_one` + the constants it needs. Verdict callers always pass their
own `preamble` and `language_code`, so the default preamble here is
intentionally empty.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

from google import genai
from google.genai import types


TTS_MODEL = "gemini-3.1-flash-tts-preview"
PREAMBLE = ""  # Verdict overrides per-call.


class NoAudioReturned(Exception):
    pass


def synth_one(
    client: genai.Client,
    voice: str,
    text: str,
    out_wav: Path,
    *,
    preamble: str | None = None,
    language_code: str = "en-US",
) -> None:
    pre = PREAMBLE if preamble is None else preamble
    prompt = pre + text.strip() + "\n"
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=TTS_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        language_code=language_code,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice,
                            )
                        ),
                    ),
                ),
            )
            try:
                data = resp.candidates[0].content.parts[0].inline_data.data
            except (AttributeError, IndexError, TypeError):
                data = None
            if not data:
                raise NoAudioReturned(
                    "model returned no audio (likely classifier rejection on short prompt)"
                )
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(data)
            return
        except Exception as exc:
            last_err = exc
            wait = 2 ** attempt
            print(f"  ⚠ {voice} attempt {attempt+1} failed ({exc}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"TTS failed for voice {voice}: {last_err}")
