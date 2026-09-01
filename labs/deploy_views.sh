#!/usr/bin/env bash
# Create the dashboard views. Run after the Terraform apply.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
: "${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
DATASET="${TRAJECTORY_DATASET:-trajectory}"
# Must match the manifest limit the detections use - v_session_labels derives
# ground truth from it, so a mismatch silently changes every precision number.
REFUND_CALL_LIMIT="${REFUND_CALL_LIMIT:-200.0}"
sed -e "s/\${PROJECT_ID}/$GOOGLE_CLOUD_PROJECT/g" -e "s/\${DATASET}/$DATASET/g" \
    -e "s/\${REFUND_CALL_LIMIT}/$REFUND_CALL_LIMIT/g" \
  sql/views/dashboard_views.sql \
  | bq --project_id="$GOOGLE_CLOUD_PROJECT" query --use_legacy_sql=false
echo "5 dashboard views created in $DATASET (4 pages + v_session_labels)"
