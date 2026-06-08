"""
HARD test PDFs — deliberately stressful input for the full pipeline + the
executive_mono_v1 2-page output template.

Each PDF (2 pages) packs: a SMALL SQUARE logo (classifies as 'logo'), several
PHOTO-shaped images (large, ~3:2 → classified 'photo' → fill the template's photo
slots), WIDE charts (→ 'chart'), data TABLES, and dense, stat-heavy prose. That
stresses: multimodal parsing (tables/charts/text → facts), image classification
(logo vs photo vs chart), per-slot image selection, grounding/faithfulness, and
the 2-page render with real photos.

Run:  python sample_data/make_hard_samples.py   ->  writes 2 PDFs to ~/Desktop
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

# reuse the proven helpers from the basic generator
from make_samples import _bar_chart_png, _font, _img, _lighten, _styles, _table  # type: ignore

OUT = Path.home() / "Desktop"


def _vgrad(w: int, h: int, top: tuple[int, int, int], bot: tuple[int, int, int]) -> PILImage.Image:
    col = PILImage.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return col.resize((w, h))


def _photo_png(variant: str, base: str, w: int = 1280, h: int = 860) -> bytes:
    """A large, ~3:2 'photographic' composition (abstract but photo-shaped)."""
    b = tuple(int(base.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    img = _vgrad(w, h, _lighten(base, 0.5), b)
    d = ImageDraw.Draw(img, "RGBA")
    lite = _lighten(base, 0.6); dark = tuple(int(c * 0.55) for c in b)
    if variant == "skyline":
        d.ellipse([w - 320, 60, w - 120, 260], fill=(*lite, 210))                      # sun/light
        for i, (x, bw, bh) in enumerate([(120, 150, 360), (300, 110, 480), (440, 170, 300),
                                          (640, 130, 560), (800, 200, 400), (1030, 150, 520)]):
            d.rectangle([x, h - bh, x + bw, h], fill=(*(dark if i % 2 else b), 255))
            for wy in range(h - bh + 30, h - 20, 46):                                  # windows
                for wx in range(x + 16, x + bw - 16, 34):
                    d.rectangle([wx, wy, wx + 16, wy + 22], fill=(*lite, 120))
    elif variant == "control":
        d.rectangle([0, 0, w, h], fill=(*dark, 255))
        for r in range(3):
            for c in range(4):
                x, y = 90 + c * 290, 110 + r * 230
                d.rounded_rectangle([x, y, x + 230, y + 160], radius=10, fill=(*b, 255))
                d.line([x + 16, y + 120, x + 80, y + 60, x + 140, y + 95, x + 210, y + 35],
                       fill=(*lite, 230), width=4)
    elif variant == "device":
        d.rounded_rectangle([w / 2 - 300, h / 2 - 190, w / 2 + 300, h / 2 + 190], radius=26, fill=(*dark, 255))
        d.rounded_rectangle([w / 2 - 250, h / 2 - 150, w / 2 + 250, h / 2 + 90], radius=12, fill=(*lite, 255))
        for i in range(5):
            d.ellipse([w / 2 - 170 + i * 80, h / 2 + 120, w / 2 - 130 + i * 80, h / 2 + 160], fill=(*lite, 200))
    elif variant == "floor":
        d.polygon([(0, h), (w, h), (w * 0.72, h * 0.5), (w * 0.28, h * 0.5)], fill=(*dark, 90))
        for x in (180, 470, 760, 1050):                                                # machinery
            d.rounded_rectangle([x, h - 360, x + 190, h - 90], radius=14, fill=(*b, 255))
            d.ellipse([x + 50, h - 150, x + 140, h - 60], fill=(*lite, 220))
    else:  # "line" — conveyor / packaging
        d.rectangle([0, h * 0.58, w, h * 0.72], fill=(*dark, 255))
        for x in range(80, w, 150):
            d.rounded_rectangle([x, h * 0.40, x + 100, h * 0.58], radius=8, fill=(*lite, 235))
        for x in range(60, w, 90):
            d.ellipse([x, h * 0.70, x + 40, h * 0.74 + 40], fill=(*dark, 255))
    bio = BytesIO(); img.save(bio, "PNG"); return bio.getvalue()


def _logo_sq(label: str, color: str, s: int = 240) -> bytes:
    img = PILImage.new("RGB", (s, s), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 14, s - 14, s - 14], radius=30, fill=color)
    d.ellipse([s * 0.30, s * 0.30, s * 0.70, s * 0.70], outline="white", width=10)
    d.text((s / 2 - len(label) * 9, s - 70), label, font=_font(34, bold=True), fill="white")
    bio = BytesIO(); img.save(bio, "PNG"); return bio.getvalue()


def make_sender():
    ss = _styles(); c = "#23303A"
    f = [
        _img(_logo_sq("AEGIS", c), 2.6), Spacer(1, 6),
        _img(_photo_png("skyline", c), 16.5), Spacer(1, 8),
        Paragraph("Aegis Industrial AI — Capability &amp; Results Dossier", ss["H"]),
        Paragraph(
            "Aegis builds industrial AI that runs on the sensors and PLCs a plant already has. We turn raw "
            "vibration, thermal, power and vision data into three outcomes manufacturers care about: less unplanned "
            "downtime, lower energy cost, and less quality scrap. Every figure in this dossier is measured from live "
            "deployments across 31 production sites in food, packaging and automotive supply.", ss["P"]),
        Paragraph("What we deploy", ss["H2"]),
        Paragraph(
            "Predictive maintenance models flag bearing, motor and gearbox failures 9–14 days before they happen; an "
            "energy-optimisation layer trims compressed-air and HVAC waste line by line; a vision quality module "
            "catches defects human inspectors miss at line speed; and a single plant dashboard unifies it all, with "
            "automated OEE and CO2 reporting. It is sensor-agnostic and installs in 6–8 weeks with no rip-and-replace.",
            ss["P"]),
        Paragraph("Outcomes from deployments (31 sites)", ss["H2"]),
        _table([["Metric", "Typical result"],
                ["Unplanned downtime", "down 41%"],
                ["Energy cost per unit", "down 19%"],
                ["Quality scrap rate", "down 32%"],
                ["Mean time between failures", "up 2.4x"],
                ["Payback period", "≈ 7 months"],
                ["Time to first value", "6–8 weeks"]], colors.HexColor(c)),
        Spacer(1, 8),
        _img(_bar_chart_png("Measured impact across 31 sites", [
            ("Downtime", "-41%", 0.41), ("Energy/unit", "-19%", 0.19),
            ("Scrap", "-32%", 0.32), ("MTBF", "+140%", 0.95)], c, w=1000, h=420), 15.5),
        PageBreak(),
        _img(_photo_png("control", c), 16.5), Spacer(1, 8),
        Paragraph("How a rollout works", ss["H2"]),
        Paragraph(
            "We start with a single line and a paid 8-week proof of value, instrumented against the plant's own "
            "baseline so the result is unarguable. A dedicated reliability engineer co-owns the pilot. Models run on "
            "an on-prem edge appliance (data never has to leave the site), with the dashboard available in the cloud "
            "or fully air-gapped for sensitive operations. Pricing is a flat per-line annual subscription — no usage "
            "metering, no long lock-in, cancel after the pilot if the numbers don't move.", ss["P"]),
        _img(_photo_png("device", c, 1180, 820), 9.5), Spacer(1, 8),
        Paragraph("Engagement model", ss["H2"]),
        _table([["Phase", "Detail"],
                ["1 · Proof of value", "1 line, 8 weeks, paid, baseline-measured"],
                ["2 · Plant rollout", "All critical lines, 1 site, ~1 quarter"],
                ["3 · Fleet", "Multi-site, central benchmarking"],
                ["Deployment", "On-prem edge + cloud or air-gapped dashboard"],
                ["Pricing", "Flat per-line annual subscription"]], colors.HexColor(c)),
        Spacer(1, 8),
        Paragraph(
            "Tone: evidence-led, pragmatic, outcome-first. We bring measured results and a reliability engineer, not "
            "hype. References available across food &amp; beverage and automotive supply.", ss["P"]),
    ]
    SimpleDocTemplate(str(OUT / "Aegis_Industrial_AI.pdf"), pagesize=A4, topMargin=1.5 * cm,
                      bottomMargin=1.5 * cm, leftMargin=1.9 * cm, rightMargin=1.9 * cm).build(f)


def make_receiver():
    ss = _styles(); c = "#2E4A39"
    f = [
        _img(_logo_sq("CONTL", c), 2.6), Spacer(1, 6),
        _img(_photo_png("floor", c), 16.5), Spacer(1, 8),
        Paragraph("Continental Foods — Operations Overview &amp; Priorities", ss["H"]),
        Paragraph(
            "Continental Foods is a European food &amp; beverage manufacturer: 14 plants across six countries, 38 "
            "production and packaging lines, roughly 9,000 employees and €3.2B in annual revenue. We make chilled and "
            "ambient products for major grocery retailers under tight service-level agreements, much of it on "
            "24/7 lines where any stoppage cascades quickly.", ss["P"]),
        Paragraph("Where we are under pressure", ss["H2"]),
        Paragraph(
            "Unplanned downtime runs at about 6.5% of scheduled run time and is our single biggest source of lost "
            "output. Energy is now roughly 12% of cost of goods and rising, with compressed air and refrigeration the "
            "worst offenders. Quality scrap sits at 3.8%. Most plants still run ageing PLCs and disconnected SCADA "
            "systems, so data is siloed and we cannot benchmark line against line. Meanwhile retailers and regulators "
            "demand auditable carbon and OEE reporting we currently assemble by hand.", ss["P"]),
        Paragraph("Footprint at a glance", ss["H2"]),
        _table([["Attribute", "Value"],
                ["Plants / countries", "14 / 6"],
                ["Production lines", "38"],
                ["Employees", "~9,000"],
                ["Revenue", "€3.2B"],
                ["Unplanned downtime", "6.5% of run time"],
                ["Energy as % of COGS", "12% (rising)"],
                ["Quality scrap", "3.8%"]], colors.HexColor(c)),
        Spacer(1, 8),
        _img(_bar_chart_png("Today's operating pain (share of impact)", [
            ("Unplanned downtime", "6.5%", 0.70), ("Energy / COGS", "12%", 0.55),
            ("Quality scrap", "3.8%", 0.40), ("Manual reporting", "high", 0.85)], c, w=1000, h=420), 15.5),
        PageBreak(),
        _img(_photo_png("line", c), 16.5), Spacer(1, 8),
        Paragraph("Our priorities this year", ss["H2"]),
        Paragraph(
            "Three things matter most: cut unplanned downtime on critical lines, take a visible bite out of energy "
            "cost per unit, and reduce quality scrap. Alongside that we must stand up credible, automated "
            "sustainability and OEE reporting for our top retail accounts. We are pragmatic, ROI-driven buyers: we "
            "will not sign a fleet-wide deal on a slide. We want a paid proof of value on one line, measured against "
            "our own baseline, and any solution must run on the sensors and PLCs we already operate — we are wary of "
            "rip-and-replace and of sending production data off-site.", ss["P"]),
        _img(_photo_png("device", c, 1180, 820), 9.5), Spacer(1, 8),
        Paragraph("Constraints any partner must respect", ss["H2"]),
        _table([["Constraint", "Detail"],
                ["Integration", "Must use existing PLCs / SCADA / sensors"],
                ["Data residency", "Sensitive lines prefer on-prem / air-gapped"],
                ["Proof", "Paid pilot on 1 line before any rollout"],
                ["Reporting", "Automated OEE + CO2 for retail accounts"],
                ["Target", "On-time service to retailers above 98%"]], colors.HexColor(c)),
    ]
    SimpleDocTemplate(str(OUT / "Continental_Foods.pdf"), pagesize=A4, topMargin=1.5 * cm,
                      bottomMargin=1.5 * cm, leftMargin=1.9 * cm, rightMargin=1.9 * cm).build(f)


if __name__ == "__main__":
    make_sender()
    make_receiver()
    print(f"wrote Aegis_Industrial_AI.pdf (sender) and Continental_Foods.pdf (receiver) to {OUT}")
