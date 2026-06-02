#!/usr/bin/env python3
"""
production/indicted/doc_synthesizer.py — Render a court-document-style PNG.

Rather than screenshot real court filings (which involves PDFs, paywalls,
cookie banners, and aspect-ratio headaches), we synthesize a clean,
visually credible "court document page" from PIL. The vertical_indicted.html
template overlays its own highlight rectangle on top using normalized
coordinates from `card.highlight`.

Output: 1080x1080 PNG, off-white "paper", court-style caption header,
the supplied quote rendered as body text with surrounding faux-text lines.
"""

from __future__ import annotations

import hashlib
import random
import re
import sys
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


# ── Font discovery (macOS-friendly) ───────────────────────────────
def _font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if mono:
        candidates = [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Courier.ttc",
            "/System/Library/Fonts/Monaco.ttf",
        ]
    elif bold:
        candidates = [
            "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
            "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
    else:
        candidates = [
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/System/Library/Fonts/Supplemental/Georgia.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Palette ────────────────────────────────────────────────────────
PAPER       = (244, 240, 230)   # warm off-white "aged paper"
PAPER_EDGE  = (228, 222, 210)
INK         = (28, 26, 24)
INK_SOFT    = (90, 86, 82)
INK_FAINT   = (170, 165, 158)
RULE        = (60, 56, 52)


def _slugify(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s_-]", "", (s or "").lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s or "doc")[:max_len]


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int,
          draw: ImageDraw.ImageDraw) -> list[str]:
    """Greedy word-wrap by measured pixel width."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        wpx = draw.textlength(trial, font=font)
        if wpx <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_paper_texture(img: Image.Image, seed: int) -> None:
    """Add subtle paper grain so the doc doesn't look like flat vector."""
    rng = random.Random(seed)
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.012)):
        x = rng.randrange(w)
        y = rng.randrange(h)
        r, g, b = px[x, y]
        d = rng.randint(-10, 4)
        px[x, y] = (max(0, r + d), max(0, g + d), max(0, b + d))


def _draw_caption_header(draw: ImageDraw.ImageDraw, *,
                         court_name: str,
                         case_no: str,
                         doc_type: str,
                         defendant_name: str,
                         w: int, top_y: int) -> int:
    """Draw the standard court-document caption block. Returns the y
    coordinate where the body should begin."""
    font_caption = _font(28, bold=True)
    font_caption_sm = _font(22, bold=False)
    font_doc_type  = _font(34, bold=True)

    # Court name (centered, all caps)
    title = court_name.upper()
    tx = (w - draw.textlength(title, font=font_caption)) / 2
    draw.text((tx, top_y), title, fill=INK, font=font_caption)
    y = top_y + 50

    # Horizontal rule
    draw.line([(80, y), (w - 80, y)], fill=RULE, width=2)
    y += 28

    # Two-column case block: case caption on left, case no on right
    left_lines = [
        "UNITED STATES OF AMERICA,",
        "       Plaintiff,",
        "",
        "                v.",
        "",
        f"{(defendant_name or 'JOHN DOE').upper()},",
        "       Defendant.",
    ]
    right_lines = [
        f"Case No. {case_no}",
        "",
        "",
        f"FILED UNDER SEAL",
    ]
    cy = y
    for line in left_lines:
        draw.text((100, cy), line, fill=INK_SOFT, font=font_caption_sm)
        cy += 26
    # vertical divider
    draw.line([(w / 2, y - 8), (w / 2, cy)], fill=INK_FAINT, width=1)
    ry = y
    for line in right_lines:
        draw.text((w / 2 + 40, ry), line, fill=INK_SOFT, font=font_caption_sm)
        ry += 26
    y = max(cy, ry) + 24

    draw.line([(80, y), (w - 80, y)], fill=RULE, width=2)
    y += 32

    # Document type (centered, large bold)
    dt = doc_type.upper()
    dx = (w - draw.textlength(dt, font=font_doc_type)) / 2
    draw.text((dx, y), dt, fill=INK, font=font_doc_type)
    y += 60
    return y


def _draw_filler_lines(draw: ImageDraw.ImageDraw, x: int, y: int,
                       max_w: int, lh: int, n: int,
                       font: ImageFont.FreeTypeFont,
                       seed: int) -> int:
    """Draw n lines of fake document text to fill space around the quote.
    Each line is a different randomized width to look like real prose."""
    rng = random.Random(seed)
    fillers = [
        "The defendant, at all relevant times described herein, knowingly and",
        "willfully engaged in conduct which violated the statutes referenced",
        "above. The investigation conducted by federal authorities established",
        "that the defendant had access to materials protected by applicable",
        "federal law. Witnesses present during the relevant period have",
        "corroborated the factual allegations set forth in the preceding",
        "paragraphs. Documentary evidence obtained pursuant to lawful process",
        "further confirms the chronology of events as alleged.",
    ]
    for i in range(n):
        line = fillers[(i + seed) % len(fillers)]
        # randomly truncate the last line to look natural
        if i == n - 1 and rng.random() < 0.6:
            line = line[: rng.randint(20, len(line) - 1)].rstrip(", ")
        draw.text((x, y), line, fill=INK_FAINT, font=font)
        y += lh
    return y


def synthesize_doc(quote: str,
                   *,
                   case_no: str,
                   court_name: str,
                   doc_type: str,
                   defendant_name: str,
                   out_path: Path,
                   page_label: str = "Page 1 of 18",
                   force: bool = False) -> Path:
    """Render a court-document image with `quote` as the highlighted body
    paragraph. Returns the absolute output path. Cached on disk."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        return out_path.resolve()

    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    # Outer page border
    draw.rectangle([(36, 36), (W - 36, H - 36)],
                   outline=PAPER_EDGE, width=4)

    # Caption header
    body_y = _draw_caption_header(
        draw,
        court_name=court_name,
        case_no=case_no,
        doc_type=doc_type,
        defendant_name=defendant_name,
        w=W, top_y=70,
    )

    body_font = _font(30, bold=False)
    body_font_bold = _font(30, bold=True)
    body_x = 100
    body_max_w = W - body_x * 2
    lh = 44

    # 2 filler lines above the quote (lead-in)
    body_y = _draw_filler_lines(draw, body_x, body_y, body_max_w, lh,
                                n=2, font=body_font,
                                seed=int(hashlib.md5(quote.encode()).hexdigest()[:4], 16))

    # Quote (highlighted-looking by being bolder + slight inset)
    body_y += 12
    quote_lines = _wrap(f"\u201C{quote}\u201D", body_font_bold, body_max_w, draw)
    for line in quote_lines:
        draw.text((body_x, body_y), line, fill=INK, font=body_font_bold)
        body_y += lh
    body_y += 12

    # 4 filler lines below the quote
    body_y = _draw_filler_lines(draw, body_x, body_y, body_max_w, lh,
                                n=4, font=body_font,
                                seed=int(hashlib.md5((quote + "x").encode()).hexdigest()[:4], 16))

    # Footer: page label centered, signature blank right
    footer_font = _font(22, bold=False)
    fw = draw.textlength(page_label, font=footer_font)
    draw.text(((W - fw) / 2, H - 70), page_label,
              fill=INK_SOFT, font=footer_font)

    # Paper texture
    _draw_paper_texture(img,
                        seed=int(hashlib.md5(quote.encode()).hexdigest()[:8], 16))

    img.save(out_path, format="PNG", optimize=True)
    return out_path.resolve()


if __name__ == "__main__":
    out = Path("/tmp/test_doc.png")
    synthesize_doc(
        "Bolton's personal email account had been compromised by an Iranian "
        "state-sponsored hacking group in 2021.",
        case_no="8:25-cr-00314",
        court_name="United States District Court for the District of Maryland",
        doc_type="Indictment",
        defendant_name="John Bolton",
        out_path=out,
        force=True,
    )
    print(f"wrote {out}")
