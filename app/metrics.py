"""
Metrics export — one row per generation, streamed to BigQuery.

This is the backbone of the org dashboard: every generation emits its quality and
cost signals (confidence, faithfulness, constraint compliance, tokens, USD,
latency) as a BigQuery row, which Looker Studio reads, row-scoped by tenant.

Same safety posture as the cache:
  * **Off by default** (`METRICS_BACKEND=none`). No-op until BigQuery is wired in.
  * **Fail-open.** A BQ outage or a schema hiccup must never fail a generation —
    the insert is wrapped; failures are logged and swallowed.
  * **Pure row builder.** `build_row()` has no I/O so it is unit-tested directly;
    the schema below is the table contract (mirror it in Terraform).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from app.config import get_settings
from app.schemas import ArticleJSON

log = logging.getLogger("collateral.metrics")

# The BigQuery table schema (keep in sync with infra/optional_metrics.tf).
TABLE_SCHEMA: list[tuple[str, str]] = [
    ("ts", "TIMESTAMP"), ("tenant", "STRING"), ("pair_id", "STRING"),
    ("request_id", "STRING"), ("source", "STRING"), ("cached", "BOOL"),
    ("model_writer", "STRING"), ("prompt_version", "STRING"), ("template_id", "STRING"),
    ("confidence", "FLOAT"), ("faithfulness", "FLOAT"), ("constraints_ok", "BOOL"),
    ("blocks_within_limits", "INTEGER"), ("total_blocks", "INTEGER"),
    ("repair_iterations", "INTEGER"), ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"), ("total_tokens", "INTEGER"),
    ("cost_usd", "FLOAT"), ("latency_ms", "INTEGER"),
]


def build_row(
    tenant: str, pair_id: str, result: ArticleJSON,
    *, latency_ms: int = 0, cached: bool = False, source: str = "generate",
) -> dict:
    """Flatten an ArticleJSON into one analytics row. Pure (no I/O)."""
    m, c, f = result.meta, result.constraints, result.faithfulness
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant": tenant, "pair_id": pair_id, "request_id": m.request_id,
        "source": source, "cached": cached,
        "model_writer": m.model_writer, "prompt_version": m.prompt_version,
        "template_id": result.template_id,
        "confidence": result.confidence, "faithfulness": f.score,
        "constraints_ok": result.constraints_ok,
        "blocks_within_limits": c.blocks_within_limits, "total_blocks": c.total_blocks,
        "repair_iterations": m.repair_iterations,
        "input_tokens": m.input_tokens, "output_tokens": m.output_tokens,
        "total_tokens": m.total_tokens,
        "cost_usd": m.cost_usd, "latency_ms": latency_ms,
    }


@lru_cache
def _bq_client():
    from google.cloud import bigquery  # lazy: only when METRICS_BACKEND=bigquery

    return bigquery.Client()


def emit_generation_metric(
    tenant: str, pair_id: str, result: ArticleJSON,
    *, latency_ms: int = 0, cached: bool = False, source: str = "generate",
) -> bool:
    """Stream one row to BigQuery. Returns True if sent, False if disabled/failed.
    Never raises — metrics must not break generation."""
    s = get_settings()
    if s.metrics_backend != "bigquery":
        return False
    row = build_row(tenant, pair_id, result, latency_ms=latency_ms, cached=cached, source=source)
    try:
        client = _bq_client()
        table = f"{client.project}.{s.bq_dataset}.{s.bq_table}"
        errors = client.insert_rows_json(table, [row])
        if errors:  # row-level rejects come back here, not as exceptions
            log.warning("metrics: BQ insert returned errors: %s", errors)
            return False
        return True
    except Exception as e:  # pragma: no cover - network/auth failure path
        log.warning("metrics: BQ insert failed (ignored): %s", e)
        return False
