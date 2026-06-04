"""
Token pricing — estimated USD cost per generation, applied per model so a draft
on Pro and repairs/verification on Flash-Lite are priced correctly (not blended).

Prices are USD per 1M tokens (input, output) and are ESTIMATES — confirm against
the Vertex / AI Studio pricing page; override here when they change. Cost is
telemetry for the dashboard, not billing truth.
"""
from __future__ import annotations

# band -> (input_per_1M, output_per_1M)
PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "pro": (1.25, 10.00),
    "flash": (0.30, 2.50),
    "flash-lite": (0.10, 0.40),
}


def band(model: str) -> str:
    m = model.lower()
    if "lite" in m:
        return "flash-lite"
    if "pro" in m:
        return "pro"
    return "flash"


def cost_usd(usage_by_model: dict[str, dict]) -> float:
    """usage_by_model: {model: {'input': n, 'output': n, ...}} -> total USD (estimate)."""
    total = 0.0
    for model, u in usage_by_model.items():
        p_in, p_out = PRICE_PER_1M[band(model)]
        total += (u.get("input", 0) / 1_000_000) * p_in + (u.get("output", 0) / 1_000_000) * p_out
    return round(total, 6)
