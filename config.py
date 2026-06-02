"""
Global configuration for the Video Essays pipeline.

Environment variables:
    GEMINI_API_KEY       — Google Gemini API key (research + scriptwriting)
    ELEVENLABS_API_KEY   — ElevenLabs API key (narration TTS)
    FAL_KEY              — fal.ai API key (AI image/video generation)
"""

import os
from pathlib import Path

# ─── Auto-load .env file if present ──────────────────────────────────────────
# .env values override any pre-existing shell exports so the file is always
# the source of truth for API keys.
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ[_key.strip()] = _val.strip()

# ─── API Keys ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
FAL_KEY = os.environ.get("FAL_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

# ─── Gemini models ────────────────────────────────────────────────────────────
MODEL_RESEARCH = "gemini-3.1-pro-preview"
MODEL_SCRIPTWRITING = "gemini-3.1-pro-preview"
# High-volume cheap scoring (catalyst scorer over hundreds of stocks/day).
# Override with env var MODEL_SCORING to A/B test against Pro.
MODEL_SCORING = os.environ.get("MODEL_SCORING", "gemini-3.1-flash-lite")

# ─── Rate limiting ────────────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS = 2.0
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2.0
SCHOLARLY_DELAY_SECONDS = 1.0

# ─── Europe PMC ───────────────────────────────────────────────────────────────
EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPEPMC_FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# ─── OpenAlex ─────────────────────────────────────────────────────────────────
OPENALEX_API_URL = "https://api.openalex.org/works"
OPENALEX_MAILTO = "videoessays@example.com"   # Polite pool — faster rate limits

# ─── Semantic Scholar ─────────────────────────────────────────────────────────
SEMANTICSCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

PAPERS_TO_SCORE = 21            # Papers to fetch per focus search (7 per source × 3)
PITCH_THRESHOLD = 7             # Papers scoring >= this get a pitch
TOP_PERCENT = 0.20              # Legacy: keep the top 20% after scoring
MAX_FIGURES_PER_PAPER = 10      # Max figure images to download per paper

# Licenses safe for commercial use (YouTube monetisation).
# Europe PMC returns lowercase strings like "cc by", "cc by-sa", "cc0".
# Any license containing "nc" (non-commercial) is NOT safe.
COMMERCIAL_SAFE_LICENSES = frozenset({
    "cc by",
    "cc by-sa",
    "cc0",
    "cc-by",
    "cc-by-sa",
})

# ─── ElevenLabs ───────────────────────────────────────────────────────────────
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_VOICE_ID = "BBfN7Spa3cqLPH1xAS22"
TTS_MODEL = "eleven_v3"
NARRATOR_WPM = 144  # Measured from Lucas voice (calibrated from per-scene TTS)
TTS_SETTINGS = {
    "stability": 0.78,
    "similarity_boost": 0.80,
    "style": 0.30,
    "use_speaker_boost": True,
    "speed": 0.8,
}

# ─── AI Visual Generation (fal.ai) ───────────────────────────────────────────
IMAGE_MODEL = "fal-ai/flux/schnell"          # Fast image generation
VIDEO_MODEL = "fal-ai/minimax/video-01"      # Text/image → video clips
IMAGE_SIZE = {"width": 1920, "height": 1080}
VIDEO_DURATION_SECONDS = 5                    # Default clip length

# ─── Video output ─────────────────────────────────────────────────────────────
OUTPUT_RESOLUTION = (1920, 1080)
OUTPUT_FPS = 30
OUTPUT_CODEC = "libx264"
OUTPUT_AUDIO_CODEC = "aac"

# ─── Output paths ─────────────────────────────────────────────────────────────
RESEARCH_OUTPUT_DIR = "research_output"
PROJECTS_DIR = "projects"


def research_dir():
    return RESEARCH_OUTPUT_DIR


def db_file():
    return os.path.join(RESEARCH_OUTPUT_DIR, "papers_db.json")


def log_file():
    return os.path.join(RESEARCH_OUTPUT_DIR, "research_log.json")


def papers_dir():
    return os.path.join(RESEARCH_OUTPUT_DIR, "papers")


def papers_fulltext_dir():
    return os.path.join(RESEARCH_OUTPUT_DIR, "papers", "fulltext")


def papers_figures_dir():
    return os.path.join(RESEARCH_OUTPUT_DIR, "papers", "figures")


def analyzed_ids_file():
    return os.path.join(RESEARCH_OUTPUT_DIR, "analyzed_paper_ids.json")


def texts_db_file():
    return os.path.join(RESEARCH_OUTPUT_DIR, "texts_db.json")


def text_scores_file():
    return os.path.join(RESEARCH_OUTPUT_DIR, "text_scores.json")


def projects_dir():
    return PROJECTS_DIR


def project_dir(project_slug: str):
    return os.path.join(PROJECTS_DIR, project_slug)
