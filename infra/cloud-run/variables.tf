variable "project_id" {
  type        = string
  description = "Google Cloud project ID."
}

variable "region" {
  type        = string
  description = "Google Cloud region for Artifact Registry and Cloud Run."
  default     = "asia-southeast1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name."
  default     = "wanderlust-backend"
}

variable "artifact_registry_repository" {
  type        = string
  description = "Artifact Registry Docker repository."
  default     = "wanderlust"
}

variable "image" {
  type        = string
  description = "Container image to deploy. Build and push this before terraform apply."
}

variable "frontend_base_url" {
  type        = string
  description = "Optional Flutter/web frontend URL for CORS."
  default     = ""
}

variable "cors_allowed_origins" {
  type        = string
  description = "Comma-separated CORS origins. Use a concrete app URL for production."
  default     = "*"
}

variable "use_vertex_ai" {
  type        = bool
  description = "Whether planner calls use Vertex AI ADC instead of Gemini API key."
  default     = false
}

variable "gemini_model" {
  type    = string
  default = "gemini-2.5-flash"
}

variable "gemini_live_model" {
  type    = string
  default = "gemini-3.1-flash-live-preview"
}

variable "booking_call_max_seconds" {
  type    = number
  default = 300
}

variable "public_backend_base_url" {
  type        = string
  description = "Optional custom domain. Leave blank to use the Cloud Run URL after a second apply."
  default     = ""
}

variable "call_log_backend" {
  type        = string
  description = "Call log backend for Cloud Run. Use firestore for redacted Twilio call status logs."
  default     = "firestore"
}

variable "call_log_collection" {
  type        = string
  description = "Firestore collection for redacted Twilio call status logs."
  default     = "wanderlust_booking_call_logs"
}

variable "wanderlust_storage_backend" {
  type        = string
  description = "Backend app-state repository backend. Use firestore in Cloud Run production."
  default     = "firestore"
}

variable "firestore_collection_prefix" {
  type        = string
  description = "Firestore collection prefix for device-scoped Wanderlust app state."
  default     = "wanderlust"
}
