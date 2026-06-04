"""
Generate two realistic, content-rich sample PDFs (a sender + a receiver) for the
prototype. Each PDF has extended text, a stats table, and THREE embedded **raster**
images (logo, hero banner, bar chart) drawn with Pillow — so PyMuPDF's image
extraction actually finds assets (logos/visuals), not just a vector mark.

Run:  python sample_data/make_samples.py
Needs reportlab + Pillow (both already project deps).
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

HERE = Path(__file__).resolve().parent


# --- Pillow image helpers (return PNG bytes) ---------------------------------
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["Arial Bold.ttf", "Helvetica.ttc"] if bold else ["Arial.ttf", "Helvetica.ttc"])
    for base in ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/", "/Library/Fonts/"):
        for n in names:
            try:
                return ImageFont.truetype(base + n, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _lighten(hex_str: str, amt: float = 0.25) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(c + (255 - c) * amt) for c in (r, g, b))  # type: ignore[return-value]


def _centered(d, box, text, font, fill):
    x0, y0, x1, y1 = box
    bb = d.textbbox((0, 0), text, font=font)
    d.text((x0 + (x1 - x0 - (bb[2] - bb[0])) / 2, y0 + (y1 - y0 - (bb[3] - bb[1])) / 2 - bb[1]),
           text, font=font, fill=fill)


def _logo_png(label: str, color: str, w: int = 540, h: int = 200) -> bytes:
    img = PILImage.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 10, w - 10, h - 10], radius=28, fill=color)
    d.ellipse([w - 150, h - 150, w - 30, h - 30], fill=_lighten(color, 0.35))
    _centered(d, (10, 6, w - 10, h - 14), label, _font(76, bold=True), "white")
    bio = BytesIO(); img.save(bio, "PNG"); return bio.getvalue()


def _hero_png(title: str, subtitle: str, color: str, w: int = 1100, h: int = 300) -> bytes:
    img = PILImage.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    d.ellipse([w - 230, -90, w + 80, 220], fill=_lighten(color, 0.22))
    d.ellipse([w - 360, 120, w - 150, 330], outline="white", width=5)
    d.line([40, h - 36, 360, h - 36], fill="white", width=4)
    d.text((46, 92), title, font=_font(62, bold=True), fill="white")
    d.text((48, 178), subtitle, font=_font(30), fill=_lighten(color, 0.7))
    bio = BytesIO(); img.save(bio, "PNG"); return bio.getvalue()


def _bar_chart_png(title: str, rows: list[tuple[str, str, float]], color: str,
                   w: int = 780, h: int = 430) -> bytes:
    img = PILImage.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((26, 20), title, font=_font(30, bold=True), fill="#10151E")
    top, bottom, left, right = 78, h - 28, 232, w - 54
    n = len(rows); gap = 20; bar_h = (bottom - top - (n - 1) * gap) / n
    for i, (label, val, frac) in enumerate(rows):
        y = top + i * (bar_h + gap)
        d.text((26, y + bar_h / 2 - 13), label, font=_font(21), fill="#475569")
        d.rounded_rectangle([left, y, right, y + bar_h], radius=9, fill="#EEF2F6")
        bw = left + (right - left) * max(0.06, min(1.0, frac))
        d.rounded_rectangle([left, y, bw, y + bar_h], radius=9, fill=color)
        d.text((min(bw + 12, right - 60), y + bar_h / 2 - 13), val, font=_font(21, bold=True), fill="#10151E")
    bio = BytesIO(); img.save(bio, "PNG"); return bio.getvalue()


def _img(png: bytes, width_cm: float) -> RLImage:
    w_px, h_px = PILImage.open(BytesIO(png)).size
    width = width_cm * cm
    return RLImage(BytesIO(png), width=width, height=width * h_px / w_px)


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H", parent=ss["Heading1"], fontSize=20, spaceAfter=10))
    ss.add(ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, spaceBefore=12, spaceAfter=6))
    ss.add(ParagraphStyle("P", parent=ss["BodyText"], fontSize=10.5, leading=15, spaceAfter=6))
    return ss


def _table(rows, header_color):
    return Table(rows, colWidths=[8 * cm, 6 * cm], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6F9")]),
        ("FONTSIZE", (0, 0), (-1, -1), 10), ("PADDING", (0, 0), (-1, -1), 6),
    ]))


def make_sender():
    ss = _styles(); teal = "#0FB5A6"
    f = [
        _img(_logo_png("NIMBUS", teal), 4.2), Spacer(1, 8),
        _img(_hero_png("Nimbus AI", "Intelligent logistics optimization, powered by ML", teal), 16.5),
        Spacer(1, 10),
        Paragraph("Nimbus AI — Company &amp; Capability Overview", ss["H"]),
        Paragraph(
            "Nimbus AI builds machine-learning systems that help fleet operators move more freight with the "
            "vehicles they already run. Our platform plugs into existing telematics and dispatch tools and pays "
            "for itself by reducing empty miles, cutting idle time, and smoothing demand peaks. We are "
            "evidence-led: every claim below is measured from live customer deployments across European road freight.",
            ss["P"]),
        Paragraph("What we offer", ss["H2"]),
        Paragraph(
            "Dynamic route optimization that re-plans in real time as orders and traffic change; demand "
            "forecasting that predicts order volume by region a week ahead; an idle-time analytics dashboard that "
            "shows exactly where vehicles wait and why; and automated carbon reporting that turns telematics data "
            "into auditable CO2 figures for customers and regulators.", ss["P"]),
        Paragraph("Why customers choose Nimbus", ss["H2"]),
        Paragraph(
            "We are telematics-agnostic, deploy in weeks rather than quarters, and price on a simple per-vehicle "
            "subscription with no long lock-in. A dedicated success engineer runs a paid pilot first, so buyers "
            "see proof on their own fleet before any rollout. Our tone is practical and outcome-first: we lead with "
            "measured results, never hype.", ss["P"]),
        Paragraph("Outcomes from deployments", ss["H2"]),
        _table([["Metric", "Typical result"],
                ["Idle vehicle time", "down 28%"],
                ["Empty miles", "down 17%"],
                ["On-time delivery", "up 12 points"],
                ["CO2 per delivery", "down 15%"],
                ["Time to first value", "3-5 weeks"]], colors.HexColor(teal)),
        Spacer(1, 10),
        _img(_bar_chart_png("Measured impact across deployments", [
            ("Idle time", "-28%", 0.28), ("Empty miles", "-17%", 0.17),
            ("On-time", "+12 pts", 0.12), ("CO2 / delivery", "-15%", 0.15)], teal), 14.5),
    ]
    SimpleDocTemplate(str(HERE / "sender_nimbus_ai.pdf"), pagesize=A4,
                      topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                      leftMargin=2 * cm, rightMargin=2 * cm).build(f)


def make_receiver():
    ss = _styles(); navy = "#1B3A5B"
    f = [
        _img(_logo_png("VANGUARD", navy), 4.6), Spacer(1, 8),
        _img(_hero_png("Vanguard Freight", "Regional road-freight, Benelux & western Germany", navy), 16.5),
        Spacer(1, 10),
        Paragraph("Vanguard Freight — Company Overview", ss["H"]),
        Paragraph(
            "Vanguard Freight is a regional road-freight carrier operating a fleet of 480 trucks across the Benelux "
            "and western Germany. We move palletized goods for retail and manufacturing clients on next-day and "
            "two-day service levels, with three cross-dock hubs and roughly 620 drivers.", ss["P"]),
        Paragraph("Where we are under pressure", ss["H2"]),
        Paragraph(
            "Dispatch is still largely manual, so vehicles sit idle while planners assign the next load — an average "
            "of 1.9 hours per vehicle per day. Rising fuel and driver costs are squeezing margins, our largest "
            "clients are demanding tighter delivery windows, and we struggle to produce the carbon reporting they "
            "increasingly require. Our planning tools are ageing and don't talk to each other.", ss["P"]),
        Paragraph("Our priorities this year", ss["H2"]),
        Paragraph(
            "Cut idle time and empty running, lift on-time performance for key accounts above 95%, and put credible "
            "numbers behind a sustainability story. We are pragmatic buyers: we want proof on a paid pilot before "
            "any fleet-wide rollout, and tools that integrate with the telematics we already run.", ss["P"]),
        Paragraph("Fleet at a glance", ss["H2"]),
        _table([["Attribute", "Value"],
                ["Vehicles", "480 trucks"],
                ["Region", "Benelux + W. Germany"],
                ["Avg. idle time / vehicle / day", "1.9 hours"],
                ["On-time delivery (current)", "86%"],
                ["Empty-running share", "22%"],
                ["Drivers", "~620"]], colors.HexColor(navy)),
        Spacer(1, 10),
        _img(_bar_chart_png("Where the fleet stands today", [
            ("On-time delivery", "86%", 0.86), ("Idle time / day", "1.9 h", 0.40),
            ("Empty running", "22%", 0.22), ("Target on-time", "95%", 0.95)], navy), 14.5),
    ]
    SimpleDocTemplate(str(HERE / "receiver_vanguard_freight.pdf"), pagesize=A4,
                      topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                      leftMargin=2 * cm, rightMargin=2 * cm).build(f)


if __name__ == "__main__":
    make_sender()
    make_receiver()
    print("wrote sender_nimbus_ai.pdf and receiver_vanguard_freight.pdf (with embedded raster images) to", HERE)
