locals {
  service_account_id    = "wanderlust-backend"
  service_account_email = "${local.service_account_id}@${var.project_id}.iam.gserviceaccount.com"
  effective_public_url  = var.public_backend_base_url != "" ? var.public_backend_base_url : "https://pending-cloud-run-url.invalid"

  secret_names = toset([
    "google-api-key",
    "google-maps-backend-api-key",
    "twilio-account-sid",
    "twilio-auth-token",
    "twilio-from-number",
  ])
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "backend" {
  location      = var.region
  repository_id = var.artifact_registry_repository
  description   = "Wanderlust backend container images"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "backend" {
  account_id   = local.service_account_id
  display_name = "Wanderlust Backend"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "firestore_writer" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

data "google_secret_manager_secret" "backend" {
  for_each  = local.secret_names
  secret_id = each.key

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "backend_access" {
  for_each  = data.google_secret_manager_secret.backend
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_cloud_run_v2_service" "backend" {
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.backend.email
    timeout         = "3600s"

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "APP_NAME"
        value = "Wanderlust Trip Backend"
      }
      env {
        name  = "BACKEND_HOST"
        value = "0.0.0.0"
      }
      env {
        name  = "BACKEND_PORT"
        value = "8080"
      }
      env {
        name  = "BACKEND_BASE_URL"
        value = local.effective_public_url
      }
      env {
        name  = "PUBLIC_BACKEND_BASE_URL"
        value = local.effective_public_url
      }
      env {
        name  = "FRONTEND_BASE_URL"
        value = var.frontend_base_url
      }
      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = var.cors_allowed_origins
      }
      env {
        name  = "USE_VERTEX_AI"
        value = tostring(var.use_vertex_ai)
      }
      env {
        name  = "VERTEX_AI_LOCATION"
        value = var.region
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "GEMINI_LIVE_MODEL"
        value = var.gemini_live_model
      }
      env {
        name  = "BOOKING_CALL_MAX_SECONDS"
        value = tostring(var.booking_call_max_seconds)
      }
      env {
        name  = "CALL_LOG_BACKEND"
        value = var.call_log_backend
      }
      env {
        name  = "CALL_LOG_COLLECTION"
        value = var.call_log_collection
      }
      env {
        name  = "WANDERLUST_STORAGE_BACKEND"
        value = var.wanderlust_storage_backend
      }
      env {
        name  = "FIRESTORE_COLLECTION_PREFIX"
        value = var.firestore_collection_prefix
      }

      dynamic "env" {
        for_each = {
          GOOGLE_API_KEY              = "google-api-key"
          GOOGLE_MAPS_BACKEND_API_KEY = "google-maps-backend-api-key"
          TWILIO_ACCOUNT_SID          = "twilio-account-sid"
          TWILIO_AUTH_TOKEN           = "twilio-auth-token"
          TWILIO_FROM_NUMBER          = "twilio-from-number"
        }

        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.backend[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.firestore_writer,
    google_secret_manager_secret_iam_member.backend_access,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.backend.location
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
