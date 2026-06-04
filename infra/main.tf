# Production stack for the Generative Collateral platform.
# Cloud Run (API) + GCS (PDFs/assets, CMEK) + Firestore (briefs/jobs/outputs) +
# Pub/Sub (async parse, DLQ) + least-privilege IAM + Vertex AI for the LLM.
# EU region throughout for data residency. Personal GCP project only.

data "google_project" "this" {}

# ── APIs ─────────────────────────────────────────────────────────────────────
locals {
  apis = [
    "run.googleapis.com", "firestore.googleapis.com", "storage.googleapis.com",
    "pubsub.googleapis.com", "cloudkms.googleapis.com", "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ── Artifact Registry (container image) ──────────────────────────────────────
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "collateral"
  format        = "DOCKER"
  depends_on    = [google_project_service.enabled]
}

# ── CMEK: customer-managed key for the bucket ────────────────────────────────
resource "google_kms_key_ring" "kr" {
  name       = "collateral-kr"
  location   = var.region
  depends_on = [google_project_service.enabled]
}

resource "google_kms_crypto_key" "bucket_key" {
  name     = "collateral-bucket-key"
  key_ring = google_kms_key_ring.kr.id
  purpose  = "ENCRYPT_DECRYPT"
}

data "google_storage_project_service_account" "gcs" {}

# the GCS service agent must be able to use the CMEK key
resource "google_kms_crypto_key_iam_member" "gcs_cmek" {
  crypto_key_id = google_kms_crypto_key.bucket_key.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

# ── GCS: raw PDFs + extracted assets (CMEK, EU, retention) ───────────────────
resource "google_storage_bucket" "assets" {
  name                        = "${var.project_id}-collateral"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  encryption {
    default_kms_key_name = google_kms_crypto_key.bucket_key.id
  }

  lifecycle_rule {
    condition { age = 365 } # retention/deletion policy — tune per tenant SLA
    action { type = "Delete" }
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs_cmek]
}

# ── Firestore: briefs / jobs / outputs ───────────────────────────────────────
resource "google_firestore_database" "db" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.enabled]
}

# ── Pub/Sub: async parse + dead-letter ───────────────────────────────────────
resource "google_pubsub_topic" "parse" {
  name       = "collateral-parse"
  depends_on = [google_project_service.enabled]
}

resource "google_pubsub_topic" "parse_dlq" {
  name = "collateral-parse-dlq"
}

# ── Service account for the API (least privilege) ────────────────────────────
resource "google_service_account" "run" {
  account_id   = "collateral-run"
  display_name = "Collateral API (Cloud Run)"
}

resource "google_project_iam_member" "run_datastore" {
  project = var.project_id
  role    = "roles/datastore.user" # Firestore
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_project_iam_member" "run_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user" # Gemini via Vertex (no API key)
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_storage_bucket_iam_member" "run_bucket" {
  bucket = google_storage_bucket.assets.name # bucket-scoped, not project-wide
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

resource "google_pubsub_topic_iam_member" "run_publish" {
  topic  = google_pubsub_topic.parse.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.run.email}"
}

# ── Cloud Run: the API (Vertex LLM path, cloud store, pubsub parse) ──────────
resource "google_cloud_run_v2_service" "api" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email
    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }
    containers {
      image = var.image
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      env {
        name  = "STORE_BACKEND"
        value = "cloud"
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.assets.name
      }
      env {
        name  = "PARSE_BACKEND"
        value = "pubsub"
      }
      env {
        name  = "PUBSUB_PARSE_TOPIC"
        value = google_pubsub_topic.parse.name
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.model_location # model endpoint (global); storage region is var.region
      }
      env {
        name  = "MODEL_WRITER"
        value = var.model_writer
      }
      env {
        name  = "MODEL_PARSER"
        value = var.model_flash
      }
      env {
        name  = "MODEL_CHEAP"
        value = var.model_flash
      }
      env {
        name  = "AUTH_MODE"
        value = "google" # production requires a verified Google ID token
      }
      env {
        name  = "AUTH_AUDIENCE"
        value = var.auth_audience
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# ── Pub/Sub push -> Cloud Run /internal/parse (OIDC, DLQ) ─────────────────────
resource "google_service_account" "pubsub_push" {
  account_id   = "collateral-push"
  display_name = "Pub/Sub push -> Cloud Run"
}

resource "google_cloud_run_v2_service_iam_member" "push_invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push.email}"
}

# Pub/Sub service agent needs to mint OIDC tokens for the push
resource "google_project_iam_member" "pubsub_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription" "parse_push" {
  name                 = "collateral-parse-push"
  topic                = google_pubsub_topic.parse.id
  ack_deadline_seconds = 120

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.api.uri}/internal/parse"
    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.parse_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}
