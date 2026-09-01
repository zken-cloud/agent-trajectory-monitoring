#!/usr/bin/env bash
# Enable native GEAP / Vertex AI request-response logging to BigQuery.
#
# WHY THIS AND NOT MORE PLUGIN CODE:
#   * zero code change - works for ANY framework, not just ADK. The most
#     portable telemetry in the whole stack.
#   * verbatim, un-truncated request AND response, which the plugin
#     deliberately does not store.
#   * lands in its own table, so raw content gets its own IAM and retention -
#     which is exactly the separation the plugin's summary+hash design assumes.
#
# SCHEMA (verified against a live project 2026-08-10 - re-verify before use):
#   endpoint STRING | deployed_model_id STRING | logging_time TIMESTAMP
#   request_id NUMERIC | request_payload ARRAY<STRING> | response_payload ARRAY<STRING>
#   model STRING | model_version STRING | api_method STRING
#   full_request JSON | full_response JSON | metadata JSON | otel_log JSON
#
# LIMITATION, MEASURED: there is NO session_id, trace_id or invocation_id in
# this table. metadata carries only request_latency; otel_log carries GenAI
# semantic-convention content records, not trace correlation - a propagated
# traceparent header does NOT appear. See ARCHITECTURE.md 4.2 for what that
# means for how you can and cannot use it.
#
# SCOPE WARNING: this is set on the PUBLISHER MODEL for the whole project and
# region. Every call to that model from anything in the project gets logged.
# Set SAMPLING below 1.0 in production, and run --disable when done.
set -euo pipefail
P="${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
R="${GOOGLE_CLOUD_LOCATION:-us-central1}"
M="${SHOPFLOW_MODEL:-gemini-3.6-flash}"
DS="${TRAJECTORY_DATASET:-trajectory}"
TBL="${GEAP_TABLE:-geap_request_response}"
SAMPLING="${SAMPLING:-1.0}"
TOKEN="$(gcloud auth print-access-token)"
URL="https://$R-aiplatform.googleapis.com/v1beta1/projects/$P/locations/$R/publishers/google/models/$M"

if [[ "${1:-}" == "--disable" ]]; then
  BODY='{"publisherModelConfig":{"loggingConfig":{"enabled":false}}}'
elif [[ "${1:-}" == "--status" ]]; then
  curl -s -H "Authorization: Bearer $TOKEN" "$URL:fetchPublisherModelConfig"; echo; exit 0
else
  BODY=$(cat <<JSON
{"publisherModelConfig":{"loggingConfig":{
  "enabled":true,"samplingRate":$SAMPLING,"enableOtelLogging":true,
  "bigqueryDestination":{"outputUri":"bq://$P.$DS.$TBL"}}}}
JSON
)
fi

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$URL:setPublisherModelConfig" -d "$BODY" >/dev/null
echo "logging config applied to $M in $R"
curl -s -H "Authorization: Bearer $TOKEN" "$URL:fetchPublisherModelConfig"; echo
echo "Table (created on first logged call): $P.$DS.$TBL"
echo "Disable with: $0 --disable"
