"""Vertical 1080x1920 court-document renderer for the Buncombe pipeline.

Unlike production/indicted/doc_synthesizer.py (1080x1080 square) this
renders large readable text designed to dominate a 9:16 mobile screen:

  - Tall portrait paper sheet
  - Large headline (court name + doc type)
  - Big body text: one short paragraph + one HIGHLIGHTED key line
  - Generous caption rule under the heading
  - Cross-platform font discovery (macOS + Ubuntu/Linux runner)
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Cross-platform font paths ───────────────────────────────────────
_REGULAR_FONTS = [
    # macOS
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Times.ttc",
    # Ubuntu / GH Actions runners (fonts-liberation, fonts-dejavu installed)
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]
_BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD_FONTS if bold else _REGULAR_FONTS):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Palette ─────────────────────────────────────────────────────────
PAPER       = (245, 241, 232)
PAPER_EDGE  = (210, 200, 184)
INK         = (24, 22, 20)
INK_SOFT    = (96, 90, 84)
RULE        = (50, 46, 42)
HIGHLIGHT_FILL = (255, 230, 96)   # yellow highlighter
HIGHLIGHT_INK  = (24, 22, 20)


def _wrap(text: str, font: ImageFont.FreeTypeFont,
          max_w: int) -> list[str]:
    """Greedy word-wrap to a given pixel width."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_paper_texture(img: Image.Image, *, seed: int = 0) -> None:
    """Subtle horizontal grain so the paper doesn't look like flat color."""
    import random
    rng = random.Random(seed)
    px = img.load()
    W, H = img.size
    for _ in range(W * H // 250):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        r, g, b = px[x, y]
        d = rng.randint(-6, 6)
        px[x, y] = (max(0, min(255, r + d)),
                    max(0, min(255, g + d)),
                    max(0, min(255, b + d)))


def render_vertical_doc(*,
                        court_name: str,
                        doc_type: str,
                        case_no: str,
                        defendant_name: str,
                        body: str,
                        highlight_excerpt: str,
                        out_path: Path,
                        force: bool = False) -> Path:
    """Render a 1080x1920 vertical court-document PNG.

    Args:
        court_name: e.g. "Buncombe County Superior Court"
        doc_type:   e.g. "Judgment of Conviction"
        case_no:    e.g. "NCDOC #1782331"
        defendant_name: e.g. "Dyana Gabrielle Sullivan"
        body:       prose paragraph (~25-60 words). Wraps to multiple lines.
        highlight_excerpt: short sentence (≤90 chars) drawn in a
            highlighter-yellow strip below the body.
        out_path:   absolute destination .png
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path.resolve()

    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    # Outer page border with a faint inner ruler
    draw.rectangle([(40, 40), (W - 40, H - 40)],
                   outline=PAPER_EDGE, width=6)
    draw.rectangle([(64, 64), (W - 64, H - 64)],
                   outline=(232, 224, 208), width=2)

    y = 150

    # ── Court name (caption) ───────────────────────────────────────
    f_court = _font(46, bold=True)
    court_text = court_name.upper()
    cw = f_court.getlength(court_text)
    draw.text(((W - cw) // 2, y), court_text, fill=INK, font=f_court)
    y += 80

    # Doc type
    f_type = _font(64, bold=True)
    tw = f_type.getlength(doc_type)
    draw.text(((W - tw) // 2, y), doc_type, fill=INK, font=f_type)
    y += 110

    # Rule
    draw.line([(140, y), (W - 140, y)], fill=RULE, width=4)
    y += 50

    # Case number + defendant
    f_meta = _font(40, bold=False)
    f_meta_b = _font(40, bold=True)
    draw.text((140, y), "State of North Carolina  v.", fill=INK_SOFT,
              font=f_meta)
    y += 60
    draw.text((140, y), defendant_name.upper(), fill=INK, font=f_meta_b)
    y += 60
    draw.text((140, y), f"Case No. {case_no}", fill=INK_SOFT, font=f_meta)
    y += 100

    # Second rule
    draw.line([(140, y), (W - 140, y)], fill=RULE, width=2)
    y += 70

    # ── Body paragraph ─────────────────────────────────────────────
    f_body = _font(46, bold=False)
    line_h = 70
    body_max_w = W - 280
    body_lines = _wrap(body, f_body, body_max_w)
    for line in body_lines:
        draw.text((140, y), line, fill=INK, font=f_body)
        y += line_h
    y += 50

    # ── Highlighted excerpt (yellow strip + bold text) ─────────────
    f_hl = _font(54, bold=True)
    hl_lines = _wrap(highlight_excerpt, f_hl, body_max_w)
    pad_x, pad_y = 28, 22

    # Compute the highlight rectangle covering all wrapped lines
    hl_total_h = len(hl_lines) * 78 + pad_y * 2 - 14
    rect_top = y - 4
    rect_bottom = rect_top + hl_total_h
    draw.rounded_rectangle(
        [(120, rect_top), (W - 120, rect_bottom)],
        radius=14, fill=HIGHLIGHT_FILL)

    yy = rect_top + pad_y
    for line in hl_lines:
        draw.text((140 + pad_x, yy), line, fill=HIGHLIGHT_INK, font=f_hl)
        yy += 78
    y = rect_bottom + 60

    # Filler lines (faint horizontal rules to evoke continued text)
    for i in range(7):
        if y + 30 > H - 200:
            break
        line_w = 760 if i % 3 != 2 else 540
        draw.line([(140, y + 6), (140 + line_w, y + 6)],
                  fill=(190, 184, 170), width=3)
        y += 56

    # Footer
    f_foot = _font(28, bold=False)
    foot = "Source: NC Department of Adult Correction · Public Offender Information"
    fw = f_foot.getlength(foot)
    draw.text(((W - fw) // 2, H - 100), foot, fill=INK_SOFT, font=f_foot)

    _draw_paper_texture(img, seed=abs(hash(case_no)) % (1 << 31))

    img.save(out_path, "PNG", optimize=True)
    return out_path.resolve()
