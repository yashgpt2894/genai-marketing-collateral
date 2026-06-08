"""
PDF parsing — the hybrid the deck describes, now storage-agnostic.

  * Deterministic bytes  -> PyMuPDF (fitz): pull embedded images/logos with their
    page + bounding box, returned AS BYTES (the caller persists them via the Store).
  * Meaning              -> Gemini multimodal reads tables/charts/text into a typed
    brief (see brief_builder.py).

Document text is *untrusted input*. `detect_injection` is a fast, blunt tripwire that
flags the most common prompt-injection phrasings — the *secondary* signal behind the
parse-time hard gate (the primary, context-aware detector is the model's own
`injection_detected` flag). The structural defence lives in generate/context.py.
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


def detect_injection(text: str) -> list[str]:
    """Fast, blunt prompt-injection tripwire: return the patterns matched in `text`
    ([] if clean). Secondary signal behind the parse-time hard gate — the primary,
    context-aware detector is the model's own `injection_detected` flag."""
    return [pat.pattern for pat in _INJECTION_PATTERNS if pat.search(text)]


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
