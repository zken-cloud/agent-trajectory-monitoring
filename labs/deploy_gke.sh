#!/usr/bin/env bash
# Lab 2.1 - deploy the SAME agent to GKE, with the trajectory plugin registered.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
: "${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
CLUSTER="${GKE_CLUSTER:-shopflow-agent}"
ADK="${ADK:-$ROOT/.venv/bin/adk}"

# `adk deploy gke` shells out to `gcloud builds submit` WITHOUT --project, so
# Cloud Build inherits the gcloud CONFIG project, not $GOOGLE_CLOUD_PROJECT.
# Symptom: the build is created in the wrong project and the push is denied on
# gcr.io/<target-project>, which reads like a permissions problem and is not.
# Pin the SDK project for this shell.
export CLOUDSDK_CORE_PROJECT="$GOOGLE_CLOUD_PROJECT"
gcloud config set project "$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1

export TRAJECTORY_SINK="${TRAJECTORY_SINK:-bigquery+pubsub}"
export TRAJECTORY_DATASET="${TRAJECTORY_DATASET:-trajectory}"
export TRAJECTORY_TOPIC="${TRAJECTORY_TOPIC:-agent-telemetry}"
export TRAJECTORY_MANIFEST="config/tool_manifest.yaml"
export TRAJECTORY_ENFORCEMENT="${TRAJECTORY_ENFORCEMENT:-shadow}"   # ALWAYS start here

# Same trap as the Agent Engine script: `adk deploy` prints "Deploy failed: ..."
# and still exits 0, so set -e does not catch it.
set -o pipefail
"$ADK" deploy gke \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$REGION" \
  --cluster_name "$CLUSTER" \
  --service_name shopflow-agent \
  --app_name shopflow \
  --with_ui \
  --trace_to_cloud \
  --otel_to_cloud \
  agent/shopflow 2>&1 | tee /tmp/adk_gke.log

if grep -qiE "deploy failed|ERROR:" /tmp/adk_gke.log; then
  echo "DEPLOY FAILED - see /tmp/adk_gke.log" >&2
  exit 1
fi
echo "Deployed to GKE. Telemetry sink: $TRAJECTORY_SINK -> $TRAJECTORY_DATASET"
