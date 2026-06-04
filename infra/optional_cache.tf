# ── Memorystore (Redis) result cache (gated by var.enable_cache) ─────────────
# A cache hit on (tenant, pair, prompt, template, brief-hash) skips the whole
# draft->repair->verify path. Cloud Run reaches Redis over a Serverless VPC Access
# connector (the gated vpc_access block lives in main.tf). Off by default.

resource "google_redis_instance" "cache" {
  count          = var.enable_cache ? 1 : 0
  name           = "collateral-cache"
  display_name   = "Collateral result cache"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region
  redis_version  = "REDIS_7_0"
  depends_on     = [google_project_service.enabled]
}

resource "google_vpc_access_connector" "cache" {
  count         = var.enable_cache ? 1 : 0
  name          = "collateral-vpc"
  region        = var.region
  network       = "default"
  ip_cidr_range = "10.8.0.0/28"
  depends_on    = [google_project_service.enabled]
}

output "redis_host" {
  value       = try(google_redis_instance.cache[0].host, "(cache disabled)")
  description = "Memorystore host the API uses as REDIS_HOST."
}
