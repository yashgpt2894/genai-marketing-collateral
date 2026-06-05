"""Image classification + role-aware slot selection (pure, no model/network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pydantic", reason="runtime deps not installed")

from app.constraints_core import (  # noqa: E402
    BlockSpec, BlockType, TemplateSpec, classify_image_kind,
)
from app.generate.images import assign_images  # noqa: E402
from app.schemas import CompanyBrief, ImageAsset  # noqa: E402


def test_classify_by_shape():
    assert classify_image_kind(120, 120) == "logo"      # small + square
    assert classify_image_kind(1200, 800) == "photo"    # large, photo-ish
    assert classify_image_kind(1600, 400) == "chart"    # very wide
    assert classify_image_kind(None, None) == "image"   # unknown dims


def _brief(role, prefix, kinds):
    return CompanyBrief(
        role=role, name=f"{prefix} co",
        images=[
            ImageAsset(id=f"{prefix}_{k}_{i}", kind=k, path="x",
                       width=(120 if k == "logo" else 1200),
                       height=(120 if k == "logo" else (400 if k == "chart" else 800)))
            for i, k in enumerate(kinds)
        ],
    )


_TPL = TemplateSpec(
    id="feature_v1", name="f", palette=("0FB5A6",),
    blocks=(
        BlockSpec("logo", BlockType.CAPTION, 2, 8, image_placeholder=True, image_role="logo"),
        BlockSpec("hero", BlockType.CAPTION, 4, 16, image_placeholder=True, image_role="photo"),
        BlockSpec("proof", BlockType.CAPTION, 6, 20, image_placeholder=True, image_role="chart"),
        BlockSpec("body", BlockType.BODY, 10, 40),   # no image slot
    ),
)


def test_assign_matches_roles_sender_first_no_reuse():
    sender = _brief("sender", "send", ["logo", "photo", "chart"])
    receiver = _brief("receiver", "recv", ["logo", "photo"])
    got = assign_images(_TPL, sender, receiver)
    assert got["logo"] == "send_logo_0"      # sender-first
    assert got["hero"] == "send_photo_1"     # photo role -> a photo
    assert got["proof"] == "send_chart_2"    # chart role -> a chart
    assert "body" not in got                 # non-image block untouched
    assert len(set(got.values())) == len(got)  # no image reused


def test_assign_leaves_slot_empty_when_no_match():
    sender = _brief("sender", "send", ["logo"])   # only a logo available
    receiver = _brief("receiver", "recv", [])
    got = assign_images(_TPL, sender, receiver)
    assert got.get("logo") == "send_logo_0"
    assert "hero" not in got and "proof" not in got   # nothing to fill them -> empty (graceful)


def test_assign_uses_receiver_as_fallback():
    sender = _brief("sender", "send", [])              # sender has nothing
    receiver = _brief("receiver", "recv", ["logo", "photo"])
    got = assign_images(_TPL, sender, receiver)
    assert got["logo"] == "recv_logo_0"
    assert got["hero"] == "recv_photo_1"
