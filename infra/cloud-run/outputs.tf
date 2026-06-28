output "service_url" {
  description = "Cloud Run service URL. Use this as BACKEND_BASE_URL in Flutter and PUBLIC_BACKEND_BASE_URL for Twilio."
  value       = google_cloud_run_v2_service.backend.uri
}

output "artifact_registry_repository" {
  description = "Docker repository for backend images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repository}"
}

output "service_account_email" {
  description = "Runtime service account."
  value       = google_service_account.backend.email
}

output "secret_names" {
  description = "Secret Manager secrets expected by the service."
  value       = sort(tolist(local.secret_names))
}
