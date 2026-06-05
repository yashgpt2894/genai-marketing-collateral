"""
Image selection — match extracted images to a template's image slots.

Each template image slot declares an `image_role` (logo | photo | chart); each
extracted image is classified by shape. This picks the best **unused** image for
each slot, leading with the **sender's** assets (it's the sender's collateral),
and leaves a slot empty rather than forcing a wrong image in. Pure + deterministic
(no model calls) — unit-tested in tests/test_images.py.
"""
from __future__ import annotations

from app.constraints_core import TemplateSpec, classify_image_kind
from app.schemas import CompanyBrief, ImageAsset

# acceptable image kinds for each slot role, best-first (graceful fallback)
_ACCEPT = {
    "logo": ["logo"],
    "photo": ["photo", "image", "chart"],
    "chart": ["chart", "photo", "image"],
}


def _effective_kind(img: ImageAsset) -> str:
    """Trust a specific stored kind; otherwise (legacy 'image') derive from shape."""
    if img.kind in ("logo", "photo", "chart"):
        return img.kind
    return classify_image_kind(img.width, img.height)


def assign_images(template: TemplateSpec, sender: CompanyBrief, receiver: CompanyBrief) -> dict[str, str]:
    """Map each image-placeholder block id -> a chosen asset id (no image reused).

    Sender's images come first (the piece is the sender's marketing collateral);
    the receiver's are the fallback. A slot with no acceptable image is left empty.
    """
    pool = list(sender.images) + list(receiver.images)   # sender-first
    used: set[str] = set()
    chosen: dict[str, str] = {}
    for spec in template.blocks:
        if not spec.image_placeholder:
            continue
        role = spec.image_role or "photo"
        for want in _ACCEPT.get(role, [role, "image"]):
            pick = next(
                (im.id for im in pool if im.id not in used and _effective_kind(im) == want),
                None,
            )
            if pick:
                chosen[spec.id] = pick
                used.add(pick)
                break
        # else: leave the slot empty — better than a wrong image
    return chosen
