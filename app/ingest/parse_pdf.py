"""
PDF parsing — the hybrid the deck describes, now storage-agnostic.

  * Deterministic bytes  -> PyMuPDF (fitz): pull embedded images/logos with their
    page + bounding box, returned AS BYTES (the caller persists them via the Store).
  * Meaning              -> Gemini multimodal reads tables/charts/text into a typed
    brief (see brief_builder.py).

Document text is *untrusted input*. `sanitize_text` does a light guard pass that
flags / neutralises the most common prompt-injection phrasings before any text is
shown to the model. (The stronger defence is structural — see generate/context.py.)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("collateral.ingest")

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all |any |the )?(previous|prior|above) instructions?", re.I),
    re.compile(r"disregard (the )?(system|previous) prompt", re.I),
    re.compile(r"\bsystem\s*:", re.I),
    re.compile(r"you are now\b", re.I),
    re.compile(r"new instructions?\s*:", re.I),
]


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """Neutralise obvious injection lines. Returns (clean_text, flags_found)."""
    flags: list[str] = []
    clean = text
    for pat in _INJECTION_PATTERNS:
        if pat.search(clean):
            flags.append(pat.pattern)
            clean = pat.sub("[removed-instruction]", clean)
    return clean, flags


@dataclass
class ExtractedAsset:
    """An image pulled from a PDF, in memory — the caller persists it via the Store."""
    id: str
    ext: str
    data: bytes
    kind: str = "image"          # "image" | "logo"
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    width: Optional[int] = None
    height: Optional[int] = None


def extract_text_and_assets(pdf_bytes: bytes, doc_prefix: str) -> tuple[str, list[ExtractedAsset]]:
    """
    Extract plain text (for sanitization preview / non-VLM fallback) and the
    embedded images as bytes. Each image is classified by shape (logo / photo /
    chart) so the layout can place the right kind of image in each template slot.

    Requires PyMuPDF (`pip install pymupdf`).
    """
    try:
        import fitz  # PyMuPDF
    except Exception as e:  # pragma: no cover
        raise ImportError("PyMuPDF is required for asset extraction: pip install pymupdf") from e

    from app.constraints_core import classify_image_kind

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts: list[str] = []
    assets: list[ExtractedAsset] = []

    for pno in range(len(doc)):
        page = doc[pno]
        texts.append(page.get_text("text"))
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue
            bbox = None
            try:
                rects = page.get_image_rects(xref)
                if rects:
                    r = rects[0]
                    bbox = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
            except Exception:
                pass
            asset = ExtractedAsset(
                id=f"{doc_prefix}_img_{pno+1}_{img_index}",
                ext=base.get("ext", "png"),
                data=base["image"],
                kind="image",
                page=pno + 1,
                bbox=bbox,
                width=base.get("width"),
                height=base.get("height"),
            )
            assets.append(asset)

    # classify each image by shape so the layout can place the right kind per slot
    for a in assets:
        a.kind = classify_image_kind(a.width, a.height)

    doc.close()
    return "\n\n".join(texts), assets
