# ── BigQuery metrics export (gated by var.enable_metrics) ────────────────────
# One row per generation (quality + cost) -> the org dashboard's source of truth,
# read by Looker Studio and row-scoped by tenant. Schema mirrors
# app/metrics.py:TABLE_SCHEMA. Off by default; flip enable_metrics=true to provision.

resource "google_bigquery_dataset" "metrics" {
  count       = var.enable_metrics ? 1 : 0
  dataset_id  = var.bq_dataset
  location    = var.region
  description = "Per-generation quality + cost metrics (Looker Studio source)."
  depends_on  = [google_project_service.enabled]
}

resource "google_bigquery_table" "generations" {
  count               = var.enable_metrics ? 1 : 0
  dataset_id          = google_bigquery_dataset.metrics[0].dataset_id
  table_id            = var.bq_table
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "ts"
  }

  schema = jsonencode([
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "tenant", type = "STRING" },
    { name = "pair_id", type = "STRING" },
    { name = "request_id", type = "STRING" },
    { name = "source", type = "STRING" },
    { name = "cached", type = "BOOL" },
    { name = "model_writer", type = "STRING" },
    { name = "prompt_version", type = "STRING" },
    { name = "template_id", type = "STRING" },
    { name = "confidence", type = "FLOAT" },
    { name = "faithfulness", type = "FLOAT" },
    { name = "constraints_ok", type = "BOOL" },
    { name = "blocks_within_limits", type = "INTEGER" },
    { name = "total_blocks", type = "INTEGER" },
    { name = "repair_iterations", type = "INTEGER" },
    { name = "input_tokens", type = "INTEGER" },
    { name = "output_tokens", type = "INTEGER" },
    { name = "total_tokens", type = "INTEGER" },
    { name = "cost_usd", type = "FLOAT" },
    { name = "latency_ms", type = "INTEGER" },
  ])
}

# the API service account streams rows into the dataset (least privilege: dataset-scoped)
resource "google_bigquery_dataset_iam_member" "run_writer" {
  count      = var.enable_metrics ? 1 : 0
  dataset_id = google_bigquery_dataset.metrics[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.run.email}"
}

output "metrics_table" {
  value       = var.enable_metrics ? "${var.project_id}.${var.bq_dataset}.${var.bq_table}" : "(metrics disabled)"
  description = "Fully-qualified BigQuery table for generation metrics."
}
