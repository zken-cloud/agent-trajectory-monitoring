variable "project_id" { type = string }
variable "region" { type = string }
variable "dataset_id" { type = string }
variable "enable_streaming" {
  type    = bool
  default = true
}
variable "labels" {
  type    = map(string)
  default = {}
}
resource "google_bigquery_dataset" "trajectory" {
  dataset_id                 = var.dataset_id
  location                   = "US"
  description                = "Agent trajectory monitoring workshop"
  labels                     = var.labels
  delete_contents_on_destroy = true
}

# Tables are created from the schema CONTRACT, not hand-written DDL:
#   python packages/agent_trajectory/agent_trajectory/schema.py trajectory
# Change a dataclass field and the DDL follows. Keeping them in sync by hand is
# how the pipeline and the detections silently drift apart.
# Partitioning field AND clustering MUST match agent_trajectory.schema.bigquery_ddl().
# The two creation paths (Terraform for attendees, schema.py for local/CI) have
# to produce identical tables - when they drifted, Terraform planned a
# delete/create on tables holding the corpus. Changing clustering forces table
# replacement in BigQuery, so a mismatch here is data loss, not a cosmetic diff.
locals {
  trajectory_tables = {
    trajectory_sessions    = { partition = "started_at", cluster = ["session_id", "agent_version"] }
    trajectory_invocations = { partition = "started_at", cluster = ["session_id", "invocation_id"] }
    trajectory_llm_calls   = { partition = "ts", cluster = ["session_id", "invocation_id"] }
    trajectory_tool_calls  = { partition = "ts", cluster = ["session_id", "tool_name"] }
  }
}

resource "google_bigquery_table" "trajectory" {
  for_each            = local.trajectory_tables
  dataset_id          = google_bigquery_dataset.trajectory.dataset_id
  table_id            = each.key
  deletion_protection = false
  schema              = file("${path.module}/schemas/${each.key}.json")
  clustering          = each.value.cluster
  time_partitioning {
    type  = "DAY"
    field = each.value.partition
  }
}

# Pub/Sub BigQuery subscriptions write PUB/SUB's schema, not yours. Pointing the
# subscription straight at trajectory_tool_calls fails with:
#   Error 400: Field `data` not found in table schema
# The message body arrives as a STRING in `data`, so it lands here first and is
# fanned out into the trajectory tables by v_telemetry_raw_parsed. That landing
# hop is normal for streaming ingestion, not a workaround.
resource "google_bigquery_table" "telemetry_raw" {
  count               = var.enable_streaming ? 1 : 0
  dataset_id          = google_bigquery_dataset.trajectory.dataset_id
  table_id            = "telemetry_raw"
  deletion_protection = false
  schema              = file("${path.module}/schemas/telemetry_raw.json")
  time_partitioning {
    type  = "DAY"
    field = "publish_time"
  }
}

resource "google_bigquery_table" "telemetry_raw_parsed" {
  count               = var.enable_streaming ? 1 : 0
  dataset_id          = google_bigquery_dataset.trajectory.dataset_id
  table_id            = "v_telemetry_raw_parsed"
  deletion_protection = false
  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        publish_time,
        JSON_VALUE(PARSE_JSON(data), '$.table')            AS target_table,
        JSON_QUERY(PARSE_JSON(data), '$.row')              AS row_json,
        JSON_VALUE(PARSE_JSON(data), '$.row.session_id')   AS session_id
      FROM `${var.project_id}.${google_bigquery_dataset.trajectory.dataset_id}.telemetry_raw`
    SQL
  }
  depends_on = [google_bigquery_table.telemetry_raw]
}

resource "google_bigquery_table" "findings" {
  dataset_id          = google_bigquery_dataset.trajectory.dataset_id
  table_id            = "findings"
  deletion_protection = false
  schema              = file("${path.module}/schemas/findings.json")
  clustering          = ["session_id", "rule_id"]
  time_partitioning {
    type  = "DAY"
    field = "detected_at"
  }
}

# --- the streaming hop -------------------------------------------------------
# Kept (not demoted to a slide) because a customer-grade pipeline needs a real
# streaming hop for sub-minute detection latency, and shipping something
# attendees would re-architect before using defeats the purpose. They do not
# hand-build it - they read the IaC that created it.
resource "google_pubsub_topic" "telemetry" {
  count  = var.enable_streaming ? 1 : 0
  name   = "agent-telemetry"
  labels = var.labels
}

resource "google_pubsub_topic" "dead_letter" {
  count = var.enable_streaming ? 1 : 0
  name  = "agent-telemetry-dlq"
}

# A BigQuery subscription is written by the Pub/Sub SERVICE AGENT, not by the
# caller, so the agent needs BigQuery write access on the dataset. Without it
# subscription creation fails with a bare "403 The caller does not have
# permission", which points at the wrong identity entirely.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_bigquery_dataset_iam_member" "pubsub_writer" {
  count      = var.enable_streaming ? 1 : 0
  dataset_id = google_bigquery_dataset.trajectory.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription" "to_bigquery" {
  count = var.enable_streaming ? 1 : 0
  name  = "agent-telemetry-to-bq"
  topic = google_pubsub_topic.telemetry[0].id

  bigquery_config {
    table            = "${var.project_id}.${google_bigquery_dataset.trajectory.dataset_id}.${google_bigquery_table.telemetry_raw[0].table_id}"
    use_topic_schema = false
    write_metadata   = false
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter[0].id
    max_delivery_attempts = 5
  }
  depends_on = [google_bigquery_dataset_iam_member.pubsub_writer]
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

output "dataset_id" { value = google_bigquery_dataset.trajectory.dataset_id }
output "topic_id" { value = try(google_pubsub_topic.telemetry[0].id, null) }
output "topic_name" { value = try(google_pubsub_topic.telemetry[0].name, "") }
