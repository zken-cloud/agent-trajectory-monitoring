output "dataset_id"      { value = module.telemetry_pipeline.dataset_id }
output "topic_id"        { value = module.telemetry_pipeline.topic_id }
output "spanner_database" { value = module.graph_store.database_name }
output "gke_cluster"     { value = try(module.agent_runtime[0].cluster_name, null) }

output "agent_env" {
  description = "Export these to point the agent at the deployed pipeline."
  value = {
    GOOGLE_CLOUD_PROJECT = var.project_id
    TRAJECTORY_SINK      = var.enable_streaming ? "bigquery+pubsub" : "bigquery"
    TRAJECTORY_DATASET   = module.telemetry_pipeline.dataset_id
    TRAJECTORY_TOPIC     = module.telemetry_pipeline.topic_name
    TRAJECTORY_MANIFEST  = "config/tool_manifest.yaml"
    TRAJECTORY_ENFORCEMENT = "shadow"   # ALWAYS start here. See LAB-GUIDE 2.6.1
  }
}

output "teardown_hint" {
  description = "Keep the data, kill the runtime - see WORKSHOP-PLAN.md 8.3."
  value = "terraform destroy -target=module.agent_runtime  # ~73% of the weekly cost"
}
