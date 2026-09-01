variable "project_id" { type = string }
variable "region"     { type = string }
variable "labels" {
  type = map(string)
  default = {}
}
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "shopflow"
  format        = "DOCKER"
  labels        = var.labels
}

# `adk deploy gke` hard-codes a push to gcr.io/<project>/<service>. In a new
# project that legacy path does not exist, and Cloud Build fails with:
#   denied: Permission 'artifactregistry.repositories.uploadArtifacts' denied
#   on resource '.../repositories/gcr.io'
# Artifact Registry serves the gcr.io hostname from a repo literally named
# "gcr.io" in the "us" multi-region, so create it.
resource "google_artifact_registry_repository" "gcr_compat" {
  location      = "us"
  repository_id = "gcr.io"
  format        = "DOCKER"
  description   = "gcr.io compatibility repo - adk deploy gke pushes here"
}

data "google_project" "runtime" {
  project_id = var.project_id
}

# Cloud Build runs as the default compute service account in new projects and
# needs to push the built image.
resource "google_project_iam_member" "build_pusher" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.runtime.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${data.google_project.runtime.number}-compute@developer.gserviceaccount.com"
}

# A freshly created project has NO "default" VPC, and GKE falls back to it
# unless told otherwise:
#   Error 400: Project "..." has no network named "default"
# Every attendee builds on a brand-new project, so relying on the default
# network breaks all of them. Found by actually running terraform apply.
resource "google_compute_network" "agent" {
  name                    = "shopflow-net"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "agent" {
  name          = "shopflow-subnet"
  region        = var.region
  network       = google_compute_network.agent.id
  ip_cidr_range = "10.20.0.0/20"
  # Private nodes reach Google APIs (Vertex, Artifact Registry, BigQuery)
  # without external IPs.
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.24.0.0/14"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.28.0.0/20"
  }
}

# STANDING DECISION: every VM in this lab is private-IP-only. Egress to Google
# APIs and the internet goes through Cloud NAT. Do not "simplify" this away.
#
# It started as a workaround - Argolis, and most enterprise orgs, enforce
# constraints/compute.vmExternalIpAccess, and Autopilot nodes take an external
# IP by default, so cluster creation fails:
#   Constraint constraints/compute.vmExternalIpAccess violated for project ...
# But it is kept on its own merits: it is the posture a customer runs, it does
# not depend on how any given project's org policy happens to be set today, and
# it costs $0.044/hr (WORKSHOP-PLAN.md 8.1). Relaxing the org policy does not
# make public nodes correct - it just hides the failure until the customer.
resource "google_compute_router" "agent" {
  name    = "shopflow-router"
  region  = var.region
  network = google_compute_network.agent.id
}

resource "google_compute_router_nat" "agent" {
  name                               = "shopflow-nat"
  router                             = google_compute_router.agent.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_container_cluster" "agent" {
  name                = "shopflow-agent"
  location            = var.region
  enable_autopilot    = true
  deletion_protection = false
  resource_labels     = var.labels

  network    = google_compute_network.agent.id
  subnetwork = google_compute_subnetwork.agent.id
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # keep the control plane reachable from laptops
  }

  depends_on = [google_compute_router_nat.agent]
  # Autopilot bills per pod REQUEST. The agent is small; do not oversize it.
  # See WORKSHOP-PLAN.md 8.3: deleting this cluster after Lab 2.2 is ~73% of
  # the weekly cost, and loses nothing anyone wants to keep.
}

resource "google_service_account" "agent" {
  account_id   = "shopflow-agent"
  display_name = "ShopFlow agent runtime"
}

# Least privilege for the telemetry path: append rows and publish. The agent
# never needs to READ the security tables it writes to.
resource "google_project_iam_member" "bq_writer" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.agent.email}"
}
resource "google_project_iam_member" "pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.agent.email}"
}
resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent.email}"
}

# The Workload Identity pool `<project>.svc.id.goog` does not exist until a
# cluster with WI has been created. Without this depends_on Terraform races and
# fails with "Identity Pool does not exist". Autopilot enables WI by default, so
# the cluster is the only thing that needs to come first.
resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.agent.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[default/shopflow-agent]"
  depends_on         = [google_container_cluster.agent]
}

output "cluster_name"    { value = google_container_cluster.agent.name }
output "service_account" { value = google_service_account.agent.email }
output "repository" { value = google_artifact_registry_repository.images.name }
