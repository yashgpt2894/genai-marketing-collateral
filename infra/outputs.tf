output "service_url" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Public URL of the API."
}

output "bucket" {
  value = google_storage_bucket.assets.name
}

output "run_service_account" {
  value = google_service_account.run.email
}

output "parse_topic" {
  value = google_pubsub_topic.parse.name
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
}
