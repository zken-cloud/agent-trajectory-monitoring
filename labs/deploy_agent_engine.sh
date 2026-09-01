#!/usr/bin/env bash
# Lab 1 - deploy the UNCHANGED ShopFlow agent to Vertex AI Agent Engine.
#
# Same code, same tools, same attacks as Scenario 2. The only thing that changes
# is how much we can see - and that difference IS the curriculum.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
: "${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
ADK="${ADK:-$ROOT/.venv/bin/adk}"

# --otel_to_cloud gives Cloud Trace spans for the agent loop with no code change.
# ADK emits OpenTelemetry natively; telemetry.googleapis.com is the OTLP endpoint.
# `adk deploy` prints "Deploy failed: ..." and STILL EXITS 0, so `set -e` does
# not catch it. Tee the output and check for the failure string, or the script
# cheerfully reports success over a broken deploy.
set -o pipefail
"$ADK" deploy agent_engine \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$REGION" \
  --display_name "ShopFlow support (workshop)" \
  --description "Scenario 1: managed runtime, native observability only" \
  --trace_to_cloud \
  --otel_to_cloud \
  agent/shopflow 2>&1 | tee /tmp/adk_deploy.log

if grep -qiE "deploy failed|traceback" /tmp/adk_deploy.log; then
  echo "DEPLOY FAILED - see /tmp/adk_deploy.log" >&2
  exit 1
fi

cat <<'MSG'

Deployed. Now run A1, A3 and A5 against it and fill in labs/gap_worksheet.md
using ONLY the native tooling:

  Cloud Trace       https://console.cloud.google.com/traces/list
  Agent Engine      https://console.cloud.google.com/vertex-ai/agents/agent-engines
  Cloud Logging     https://console.cloud.google.com/logs

Do not open BigQuery. The point of this lab is what you CANNOT see.
MSG
