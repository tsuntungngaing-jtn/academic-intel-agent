"""
Heuristic extraction of figure images near captions that mention Fig/Figure + layout keywords.

Uses PyMuPDF (``fitz``): text blocks vs image bboxes, vertical proximity, optional fallback to largest image.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
except ImportError as e:  # pragma: no cover
    raise ImportError("pdf_visuals requires pymupdf: pip install pymupdf") from e

HEURISTIC_KEYWORDS = [
    "schematic",
    "diagram",
    "layout",
    "setup",
    "architecture",
    "principle",
]

# Max vertical gap (PDF points ≈ pixels at 72 dpi) between caption and figure
VERTICAL_DISTANCE_THRESHOLD = 250

_FIG_PATTERN = re.compile(r"\bfig(?:ure)?\b", re.IGNORECASE)


def _default_figures_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "figures"


def sanitize_doi_for_filename(doi: str) -> str:
    """Strip URL prefix and replace characters unsafe in file names."""
    s = (doi or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    for ch in r'/\\:*?"<>|':
        s = s.replace(ch, "_")
    s = s.replace(" ", "_")
    return s or "unknown_doi"


def _block_text_and_bbox(block: dict[str, Any]) -> tuple[str, Optional[fitz.Rect]]:
    if block.get("type") != 0:
        return "", None
    bbox = block.get("bbox")
    if not bbox or len(bbox) < 4:
        return "", None
    r = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
    parts: list[str] = []
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            t = span.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "".join(parts), r


def _first_matching_keyword(text: str) -> Optional[str]:
    if not _FIG_PATTERN.search(text):
        return None
    lower = text.lower()
    for kw in HEURISTIC_KEYWORDS:
        if kw.lower() in lower:
            return kw
    return None


def _vertical_gap_between_caption_and_image(text_rect: fitz.Rect, img_rect: fitz.Rect) -> float:
    """
    Gap when image is above caption (Fig below figure): text.y0 - img.y1.
    When image is below caption: img.y0 - text.y1.
    Overlap → 0.
    """
    if img_rect.y1 <= text_rect.y0:
        return float(text_rect.y0 - img_rect.y1)
    if img_rect.y0 >= text_rect.y1:
        return float(img_rect.y0 - text_rect.y1)
    return 0.0


def _x_center_in_text_band(cx: float, text_rect: fitz.Rect) -> bool:
    return text_rect.x0 <= cx <= text_rect.x1


def _collect_page_image_placements(page: fitz.Page) -> list[tuple[fitz.Rect, int]]:
    """List of (rect on page, xref) for each image placement."""
    placements: list[tuple[fitz.Rect, int]] = []
    seen: set[tuple[float, float, float, float, int]] = set()
    for info in page.get_images(full=True):
        xref = int(info[0])
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for rect in rects:
            key = (round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2), xref)
            if key in seen:
                continue
            seen.add(key)
            placements.append((rect, xref))
    return placements


def _rect_area(r: fitz.Rect) -> float:
    return float(abs(r.width) * abs(r.height))


def _largest_image_on_page(placements: list[tuple[fitz.Rect, int]]) -> Optional[tuple[fitz.Rect, int]]:
    if not placements:
        return None
    return max(placements, key=lambda p: _rect_area(p[0]))


def _match_image_for_caption(
    text_rect: fitz.Rect,
    placements: list[tuple[fitz.Rect, int]],
    *,
    threshold: float = VERTICAL_DISTANCE_THRESHOLD,
) -> Optional[tuple[fitz.Rect, int]]:
    candidates: list[tuple[float, float, fitz.Rect, int]] = []
    for img_rect, xref in placements:
        cx = (img_rect.x0 + img_rect.x1) / 2.0
        if not _x_center_in_text_band(cx, text_rect):
            continue
        gap = _vertical_gap_between_caption_and_image(text_rect, img_rect)
        if gap < threshold:
            candidates.append((gap, -_rect_area(img_rect), img_rect, xref))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    _, _, rect, xref = candidates[0]
    return rect, xref


def _export_clip_as_jpg(page: fitz.Page, rect: fitz.Rect, dest: Path, zoom: float = 2.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
    if pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(str(dest))


def extract_heuristic_figures(
    pdf_path: Path | str,
    doi: str,
    *,
    out_dir: Optional[Path] = None,
    vertical_threshold: float = VERTICAL_DISTANCE_THRESHOLD,
) -> list[Path]:
    """
    Scan ``pdf_path`` for caption-like text (Fig/Figure + heuristic keyword), match nearby images,
    save as ``{sanitized_doi}_Fig_{keyword}.jpg`` under ``out_dir`` (default ``data/figures/``).

    When no image meets distance + X-alignment rules on a page that has multiple images,
    falls back to the **largest** image bbox on that page (first among ties by area).

    Returns paths of written JPEG files (deduped by destination path).
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    out_base = out_dir or _default_figures_dir()
    doi_part = sanitize_doi_for_filename(doi)

    written: list[Path] = []
    used_names: dict[str, int] = {}

    doc = fitz.open(path)
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            placements = _collect_page_image_placements(page)
            if not placements:
                continue

            raw = page.get_text("dict")
            blocks = raw.get("blocks") or []

            matched_blocks: list[tuple[fitz.Rect, str]] = []
            for block in blocks:
                text, bbox = _block_text_and_bbox(block)
                if bbox is None:
                    continue
                kw = _first_matching_keyword(text)
                if kw:
                    matched_blocks.append((bbox, kw))

            ambiguous_page = len(placements) > 1

            for text_rect, keyword in matched_blocks:
                match = _match_image_for_caption(
                    text_rect, placements, threshold=vertical_threshold
                )
                if match is None and ambiguous_page:
                    match = _largest_image_on_page(placements)
                    if match:
                        logger.info(
                            "Page %s: no proximity match for keyword %r; using largest image fallback",
                            page_index + 1,
                            keyword,
                        )
                elif match is None and len(placements) == 1:
                    match = placements[0]
                if match is None:
                    continue
                img_rect, _xref = match

                base_name = f"{doi_part}_Fig_{keyword}"
                n = used_names.get(base_name, 0)
                used_names[base_name] = n + 1
                fname = f"{base_name}.jpg" if n == 0 else f"{base_name}_{n + 1}.jpg"
                dest = out_base / fname
                try:
                    _export_clip_as_jpg(page, img_rect, dest)
                    written.append(dest)
                except Exception as e:
                    logger.warning("Failed to export %s: %s", dest, e)
    finally:
        doc.close()

    return written
