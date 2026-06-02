"""Pydantic schemas for research, scriptwriting, and production."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─── Research schemas ─────────────────────────────────────────────────────────


class ScholarlyPaper(BaseModel):
    """A single paper harvested from Europe PMC."""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    title: str = ""
    authors: str = ""
    year: int = 0
    journal: str = ""
    source: str = ""
    abstract: str = ""
    license: str = Field(default="", description="Creative Commons license string from Europe PMC, e.g. 'cc by'")
    full_text_path: str = ""
    figure_paths: list[str] = Field(default_factory=list, description="Paths to downloaded figure images")
    figure_captions: list[str] = Field(default_factory=list, description="Captions for each figure")
    search_topic: str = Field(default="", description="Which topic config found this paper")
    relevance_tags: list[str] = Field(default_factory=list, description="Keywords matched")


class TopicConfig(BaseModel):
    """A research topic with search queries for Europe PMC and OpenAlex."""
    id: str = Field(description="Short slug, e.g. 'crispr-ethics'")
    name: str = Field(description="Display name, e.g. 'CRISPR Gene Editing Ethics'")
    queries: list[str] = Field(description="Europe PMC search queries")
    openalex_queries: list[str] = Field(
        default_factory=list,
        description="OpenAlex plain-text search queries",
    )
    semanticscholar_queries: list[str] = Field(
        default_factory=list,
        description="Semantic Scholar search queries (broader coverage of niche journals & preprints)",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords for relevance matching in title/abstract",
    )
    description: str = Field(default="", description="Brief description of the topic angle")


class PapersDB(BaseModel):
    """Database of all harvested scholarly papers."""
    papers: list[ScholarlyPaper] = Field(default_factory=list)
    topics: list[TopicConfig] = Field(default_factory=list)


class WebText(BaseModel):
    """A web text harvested via Gemini search grounding."""
    url: str = ""
    title: str = ""
    content: str = ""
    domain: str = ""
    search_topic: str = Field(default="", description="Topic slug that found this text")
    search_author: str = Field(default="", description="Author name if fetched via by-author mode")
    fetched_at: str = ""


class TextsDB(BaseModel):
    """Database of all harvested web texts."""
    texts: list[WebText] = Field(default_factory=list)


# ─── Script schemas ──────────────────────────────────────────────────────────


class SceneVisual(BaseModel):
    """Visual description for a single scene."""
    prompt: str = Field(description="Image/video generation prompt")
    style: Literal["photograph", "illustration", "diagram", "animation", "archival", "paper_figure", "video", "d3_animation"] = "photograph"
    motion: str = Field(
        default="slow_zoom_in",
        description="Camera motion: slow_zoom_in, slow_zoom_out, pan_left, pan_right, static, "
                    "or 'video' for AI-generated video clip",
    )
    paper_figure_path: str = Field(
        default="",
        description="Path to a harvested paper figure image. When set, this is used instead of AI generation.",
    )
    paper_doi: str = Field(
        default="",
        description="DOI of the paper this figure comes from (for attribution)",
    )
    d3_template: str = Field(
        default="",
        description="D3/Canvas HTML template filename in d3_scenes/ (e.g. 'migration_map.html')",
    )
    d3_config: dict = Field(
        default_factory=dict,
        description="Template-specific configuration for D3 rendering",
    )


class SceneInset(BaseModel):
    """A supplementary image overlay that appears during a scene."""
    trigger_time: float = Field(description="Seconds into the scene when the inset appears")
    duration: float = Field(default=6.0, description="How long the inset stays on screen")
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "bottom_right"
    scale: float = Field(default=0.25, description="Fraction of frame width (0.15–0.35)")
    image_path: str = Field(default="", description="Local path to the inset image")
    caption: str = Field(default="", description="Short caption displayed below the inset")
    source: Literal["paper_figure", "web_photo", "creative_commons"] = "web_photo"
    source_url: str = Field(default="", description="Original URL of the image (for attribution)")
    source_attribution: str = Field(default="", description="Credit line, e.g. 'Photo: Museum of Archaeology'")
    search_query: str = Field(default="", description="Query used to find this image (for reproducibility)")


class ScriptScene(BaseModel):
    """A single scene in a video essay script."""
    id: str = Field(description="Unique scene identifier, e.g. 'intro', 'finding_01'")
    title: str = Field(default="", description="Scene heading for reference")
    narration: str = Field(default="", description="Narration text (read aloud)")
    duration: float = Field(default=8.0, description="Estimated duration in seconds")
    visual: SceneVisual = Field(default_factory=SceneVisual)
    insets: list[SceneInset] = Field(default_factory=list, description="Supplementary image overlays")
    transition: Literal["cut", "crossfade", "fade_black"] = "crossfade"
    transition_duration: float = 0.5


class VideoScript(BaseModel):
    """Complete video essay script."""
    title: str = Field(description="YouTube video title")
    description: str = Field(default="", description="YouTube video description")
    tags: list[str] = Field(default_factory=list, description="YouTube tags")
    hook: str = Field(
        default="",
        description="Opening hook — the first 5-10 seconds that grab attention",
    )
    scenes: list[ScriptScene] = Field(default_factory=list)
    source_papers: list[str] = Field(
        default_factory=list,
        description="DOIs or titles of papers referenced",
    )
    total_duration: float = Field(default=0.0)

    def compute_duration(self):
        self.total_duration = sum(s.duration for s in self.scenes)
        return self.total_duration


# ─── Production schemas ──────────────────────────────────────────────────────


class GeneratedAsset(BaseModel):
    """A single generated visual asset (image or video clip)."""
    scene_id: str
    asset_type: Literal["image", "video"] = "image"
    file_path: str = ""
    prompt_used: str = ""
    generation_model: str = ""


class AudioSegment(BaseModel):
    """A narration audio clip for a scene."""
    scene_id: str
    file_path: str = ""
    duration: float = 0.0
    words: int = 0


class ProductionManifest(BaseModel):
    """Tracks all generated assets for a project."""
    project_slug: str
    script_path: str = ""
    voice_id: str = ""
    tts_model: str = ""
    wpm: float = 0.0
    total_audio_duration: float = 0.0
    audio_segments: list[AudioSegment] = Field(default_factory=list)
    visual_assets: list[GeneratedAsset] = Field(default_factory=list)
    final_video_path: str = ""
