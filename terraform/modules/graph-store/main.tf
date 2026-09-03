variable "project_id" { type = string }
variable "region" { type = string }
variable "processing_units" {
  type    = number
  default = 100
}
variable "labels" {
  type    = map(string)
  default = {}
}
resource "google_spanner_instance" "graph" {
  name         = "trajectory-graph"
  config       = "regional-${var.region}"
  display_name = "Agent trajectory graph"

  # ENTERPRISE IS MANDATORY, NOT AN UPGRADE.
  # Spanner Graph is an Enterprise-edition feature. On STANDARD the CREATE
  # PROPERTY GRAPH statement fails outright:
  #   "Feature GRAPH is not available to Instance ... in Edition STANDARD.
  #    The minimum required Edition for this feature is ENTERPRISE."
  # The whole schema apply fails, so the graph lab is dead before it starts.
  # Verified against a live instance 2026-08-10.
  edition = "ENTERPRISE"
  # 100 PU is the Spanner minimum and ample for ~200k edges (~$0.09/hr).
  # This pin is the highest-leverage cost guardrail in the build.
  processing_units = var.processing_units
  labels           = var.labels
}

resource "google_spanner_database" "graph" {
  instance = google_spanner_instance.graph.name
  name     = "trajectory"
  # DDL is the single source of truth in spanner/schema.sql - split on the
  # statement separator so the file stays readable and reviewable as SQL.
  ddl = [
    for stmt in split(";", replace(file("${path.module}/../../../spanner/schema.sql"),
    "/(?m)^\\s*--.*$/", "")) :
    trimspace(stmt) if trimspace(stmt) != ""
  ]
  deletion_protection = false

  # Spanner DDL is append-only and Terraform tracks the whole statement list.
  # On any re-apply against an existing database it tries to replay CREATE TABLE
  # and fails with "Duplicate name in schema: Agent". A fresh create still gets
  # the full schema; subsequent migrations are applied out of band with
  # `gcloud spanner databases ddl update`.
  lifecycle {
    ignore_changes = [ddl]
  }
}

output "instance_name" { value = google_spanner_instance.graph.name }
output "database_name" { value = google_spanner_database.graph.name }
