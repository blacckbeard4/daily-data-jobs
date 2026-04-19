"""
Generate a 1200x627px PNG header image for each daily LinkedIn post.
Uses Pillow only — no external APIs, zero cost.

Output: /tmp/ddj_header_{YYYYMMDD}.png  (or DRYRUN in dry-run mode)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from config.settings import CATEGORY_ORDER
from utils.logger import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Design constants
# ---------------------------------------------------------------------------

W, H = 1200, 627
PAD = 60

C_BG      = "#0F1117"
C_WHITE   = "#FFFFFF"
C_TEAL    = "#00C896"
C_GRAY    = "#8B8FA8"
C_PILL_BG = "#1E2130"

PILL_H = 38

CATEGORY_LABELS: dict[str, str] = {
    "Data Engineer":  "DATA ENGINEER",
    "Data Analyst":   "DATA ANALYST",
    "ML Engineer":    "ML ENGINEER",
    "Data Scientist": "DATA SCIENTIST",
    "AI Engineer":    "AI ENGINEER",
}

# ---------------------------------------------------------------------------
# Font loader — tries Ubuntu paths first, then macOS, then Pillow default
# ---------------------------------------------------------------------------

_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("No TrueType font found — using Pillow default bitmap font.")
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

_SALARY_RE = re.compile(r"\$([\d,]+)([Kk])?")


def _parse_salary_value(salary_info: str | None) -> int:
    """Return the highest dollar amount found in the salary string, or 0."""
    if not salary_info:
        return 0
    best = 0
    for m in _SALARY_RE.finditer(salary_info):
        raw = int(m.group(1).replace(",", ""))
        if m.group(2):
            raw *= 1000
        if raw >= 1000:  # ignore bare values like "$124"
            best = max(best, raw)
    return best


def _find_hero_job(ranked: dict) -> dict | None:
    """Return the job with the highest explicit salary, or the first available job."""
    best_job: dict | None = None
    best_val = 0
    for cat in CATEGORY_ORDER:
        for job in ranked.get(cat, []):
            val = _parse_salary_value(job.get("salary_info"))
            if val > best_val:
                best_val = val
                best_job = job
    if best_job:
        return best_job
    for cat in CATEGORY_ORDER:
        jobs = ranked.get(cat, [])
        if jobs:
            return jobs[0]
    return None


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if _tw(draw, text, font) <= max_w:
        return text
    while text and _tw(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def _draw_pill(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font) -> int:
    """Draw a rounded-rect pill; return its pixel width."""
    px = 18
    tw = _tw(draw, text, font)
    pill_w = tw + px * 2
    draw.rounded_rectangle([x, y, x + pill_w, y + PILL_H], radius=PILL_H // 2, fill=C_PILL_BG)
    text_y = y + (PILL_H - _th(draw, text, font)) // 2
    draw.text((x + px, text_y), text, font=font, fill=C_GRAY)
    return pill_w


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_image(ranked: dict, dry_run: bool = False) -> str | None:
    """
    Generate a 1200x627 header PNG and save to /tmp.

    Returns the file path on success, or None on failure (non-fatal).
    """
    try:
        return _render(ranked, dry_run)
    except Exception as exc:
        logger.error(f"Image generation failed: {exc}")
        return None


def _render(ranked: dict, dry_run: bool) -> str:
    date_str = "DRYRUN" if dry_run else datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = f"/tmp/ddj_header_{date_str}.png"

    img = Image.new("RGB", (W, H), color=C_BG)
    draw = ImageDraw.Draw(img)

    font_label   = _load_font(_REGULAR_PATHS, 16)
    font_tiny    = _load_font(_REGULAR_PATHS, 15)
    font_body    = _load_font(_REGULAR_PATHS, 28)
    font_company = _load_font(_BOLD_PATHS, 58)
    font_salary  = _load_font(_BOLD_PATHS, 52)
    font_pill    = _load_font(_REGULAR_PATHS, 14)

    hero = _find_hero_job(ranked)
    max_content_w = W - PAD * 2

    # --- Branding (top-right) ---
    brand = "Daily Data Jobs"
    draw.text((W - PAD - _tw(draw, brand, font_label), 45), brand, font=font_label, fill=C_GRAY)

    # --- Section label ---
    draw.text((PAD, 70), "Today's top data roles", font=font_label, fill=C_GRAY)

    # --- Hero block ---
    if hero:
        company = _truncate(draw, hero.get("company", "Top Company"), font_company, max_content_w)
        title   = _truncate(draw, hero.get("title",   "Data Role"),   font_body,    max_content_w)
        salary  = hero.get("salary_info") or ""

        draw.text((PAD, 115), company, font=font_company, fill=C_WHITE)
        draw.text((PAD, 188), title,   font=font_body,    fill=C_WHITE)

        if salary and salary.lower() not in ("", "undisclosed"):
            draw.text((PAD, 235), salary, font=font_salary, fill=C_TEAL)

    # --- Category pills (centered row) ---
    pill_y   = 360
    pill_gap = 12
    active   = [cat for cat in CATEGORY_ORDER if ranked.get(cat)]
    labels   = [CATEGORY_LABELS[cat] for cat in active]

    # Pre-calculate pill widths for centering
    pill_widths = [_tw(draw, lbl, font_pill) + 36 for lbl in labels]
    total_w = sum(pill_widths) + pill_gap * (len(labels) - 1)
    x = (W - total_w) // 2
    for lbl, pw in zip(labels, pill_widths):
        _draw_pill(draw, x, pill_y, lbl, font_pill)
        x += pw + pill_gap

    # --- Divider ---
    draw.line([(PAD, 468), (W - PAD, 468)], fill=C_PILL_BG, width=1)

    # --- Bottom bar ---
    bottom = "Updated daily at 8AM ET  ·  #DataJobs"
    draw.text(((W - _tw(draw, bottom, font_tiny)) // 2, 490), bottom, font=font_tiny, fill=C_GRAY)

    img.save(out_path, "PNG")
    logger.info(f"Header image saved: {out_path}")
    return out_path
