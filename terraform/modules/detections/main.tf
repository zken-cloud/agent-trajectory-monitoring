variable "project_id" { type = string }
variable "region"     { type = string }
variable "dataset_id" { type = string }

# Scheduled queries run unattended, so they need an identity of their own:
#   Error 400: Failed to find a valid credential. The field 'version_info' or
#   'service_account_name' must be specified.
# Terraform cannot mint the OAuth 'version_info' flow, so a service account is
# the only option that works non-interactively.
resource "google_service_account" "scheduler" {
  account_id   = "trajectory-detections"
  display_name = "Runs the detection ladder on a schedule"
}

resource "google_project_iam_member" "scheduler_bq" {
  project = var.project_id
  role    = "roles/bigquery.admin"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# The caller must be able to act as the SA to attach it to a transfer config.
resource "google_service_account_iam_member" "scheduler_token" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.scheduler.email}"
}

# Scheduled queries run the detection ladder on a cadence. Layer 1 FIRST: its
# findings exclude those sessions from the Layer 2 baseline, and a baseline
# fitted on traffic containing the attacks learns the attacks as normal.
locals {
  layer1_rules = ["d1_policy_violation", "d2b_refined", "d3_tool_loop",
                  "d4_refund_aggregate", "d5_off_manifest"]
}

# WRITE_APPEND + a schedule means the SAME finding is re-inserted on every run:
# at 15 minutes that is 4 copies an hour and ~670 in a week, in every attendee's
# project. The rules are pure SELECTs shared with run_detections.sh and the
# DuckDB validator, so rewriting them as MERGE would break both. Instead:
#   - read through v_findings, never `findings` (sql/views/dashboard_views.sql)
#   - the janitor below removes the duplicates on a schedule
#   - and 15 minutes was faster than anything here needs; hourly is plenty for a
#     corpus that only changes when someone runs the traffic generator.
resource "google_bigquery_data_transfer_config" "layer1" {
  for_each               = toset(local.layer1_rules)
  display_name           = "layer1-${each.value}"
  data_source_id         = "scheduled_query"
  schedule               = "every 1 hours"
  destination_dataset_id = var.dataset_id
  location               = "US"
  service_account_name   = google_service_account.scheduler.email
  params = {
    query = templatefile("${path.module}/../../../sql/layer1/${each.value}.sql", {
      PROJECT_ID         = var.project_id
      DATASET            = var.dataset_id
      REFUND_CALL_LIMIT  = "200.0"
      REFUND_SESSION_CAP = "300.0"
      EGRESS_ALLOWLIST   = "shopflow\\.example\\.com|shopflow-support\\.example\\.com"
      LOOP_THRESHOLD     = "5"
      LOOKBACK           = "10"
    })
    destination_table_name_template = "findings"
    write_disposition               = "WRITE_APPEND"
  }
}

resource "google_bigquery_data_transfer_config" "layer2_surprisal" {
  display_name           = "layer2-transition-surprisal"
  data_source_id         = "scheduled_query"
  schedule               = "every 6 hours"     # after Layer 1 has populated findings
  destination_dataset_id = var.dataset_id
  location               = "US"
  service_account_name   = google_service_account.scheduler.email
  params = {
    query = templatefile("${path.module}/../../../sql/layer2/transition_surprisal.sql", {
      PROJECT_ID = var.project_id
      DATASET    = var.dataset_id
      # Calibrated, not guessed: benign p99 = 6.94, max = 8.05 on the shipped
      # corpus. 8.0 catches A5 at a 0.25% false positive rate. See
      # sql/layer2/transition_surprisal.sql for the full measurement.
      SURPRISAL_THRESHOLD = "8.0"
      SURPRISAL_HIGH      = "15.0"
      UNSEEN_PROB         = "0.001"
    })
    destination_table_name_template = "findings"
    write_disposition               = "WRITE_APPEND"
  }
}


# Keeps `findings` from growing without bound. Deletes every row that is not the
# most recent for its (rule_id, session_id) - which is exactly what v_findings
# already hides at read time, made durable so storage does not creep either.
# DML, so no destination table: a scheduled query with no destination runs the
# statement as-is.
resource "google_bigquery_data_transfer_config" "findings_dedupe" {
  display_name         = "findings-dedupe"
  data_source_id       = "scheduled_query"
  schedule             = "every 3 hours"
  location             = "US"
  service_account_name = google_service_account.scheduler.email
  params = {
    # Rewrite-in-place inside a transaction, keeping one row per finding_id.
    # A plain DELETE cannot express "keep any one of these": detected_at is the
    # tool call's timestamp, not the write time, so duplicate rows are
    # byte-identical and MAX(detected_at) matches all of them. CREATE OR REPLACE
    # TABLE is not an option either - it would drop the partitioning and
    # clustering Terraform defines on findings.
    query = <<-SQL
      BEGIN TRANSACTION;
      CREATE TEMP TABLE _keep AS
        SELECT * EXCEPT (rn) FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY finding_id
                                       ORDER BY detected_at) AS rn
          FROM `${var.project_id}.${var.dataset_id}.findings`)
        WHERE rn = 1;
      DELETE FROM `${var.project_id}.${var.dataset_id}.findings` WHERE TRUE;
      INSERT INTO `${var.project_id}.${var.dataset_id}.findings`
        SELECT * FROM _keep;
      COMMIT TRANSACTION;
    SQL
  }
}
