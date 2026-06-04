variable "project_id" {
  type        = string
  description = "Your PERSONAL GCP project (never an Infosys/LG project)."
}

variable "region" {
  type        = string
  default     = "europe-west1" # EU data residency
  description = "Region for Cloud Run, GCS, Firestore, Pub/Sub, KMS."
}

variable "service_name" {
  type    = string
  default = "collateral-api"
}

variable "image" {
  type        = string
  description = "Container image in Artifact Registry, e.g. europe-west1-docker.pkg.dev/PROJECT/collateral/api:v1"
}

variable "model_writer" {
  type    = string
  default = "gemini-2.5-pro" # available via the 'global' endpoint for this project
}

variable "model_flash" {
  type    = string
  default = "gemini-2.5-flash"
}

variable "model_location" {
  type        = string
  default     = "global" # europe-west1 serves no Gemini for this project; storage stays in var.region
  description = "Vertex location for Gemini model calls ('global' or a region that serves the models)."
}

variable "auth_audience" {
  type        = string
  default     = ""
  description = "Expected audience for Google ID tokens (the service URL). Empty = skip audience check."
}
