# Modularised BY CONCERN, not by lab. An attendee lifts the two modules their
# customer needs without unpicking lab sequencing (WORKSHOP-PLAN 0).

module "telemetry_pipeline" {
  source           = "./modules/telemetry-pipeline"
  project_id       = var.project_id
  region           = var.region
  dataset_id       = var.dataset_id
  enable_streaming = var.enable_streaming
  labels           = var.labels
}

module "graph_store" {
  source           = "./modules/graph-store"
  project_id       = var.project_id
  region           = var.region
  processing_units = var.spanner_processing_units
  labels           = var.labels
}

module "agent_runtime" {
  count      = var.enable_gke ? 1 : 0
  source     = "./modules/agent-runtime"
  project_id = var.project_id
  region     = var.region
  labels     = var.labels
}

module "detections" {
  source     = "./modules/detections"
  project_id = var.project_id
  region     = var.region
  dataset_id = module.telemetry_pipeline.dataset_id
}

module "dashboards" {
  source     = "./modules/dashboards"
  project_id = var.project_id
  dataset_id = module.telemetry_pipeline.dataset_id
}
