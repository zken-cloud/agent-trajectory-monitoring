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

# The non-security tier. These read the NATIVE plane (agent_events and the
# views the ADK BigQuery Agent Analytics plugin creates), so they only exist
# once Path A has actually run - see LAB-GUIDE 2.1.2. Skipped rather than
# failed when it has not, because the security labs do not depend on them.
have() { bq --project_id="$GOOGLE_CLOUD_PROJECT" show "$DATASET.$1" >/dev/null 2>&1; }
if have agent_events && have trajectory_sessions; then
  sed -e "s/\${PROJECT_ID}/$GOOGLE_CLOUD_PROJECT/g" -e "s/\${DATASET}/$DATASET/g" \
    sql/views/operations_views.sql \
    | bq --project_id="$GOOGLE_CLOUD_PROJECT" query --use_legacy_sql=false
  echo "3 operations views created in $DATASET (cost, quality, tool health)"
else
  echo "SKIPPED operations views - they join BOTH planes and one is missing:"
  have agent_events       || echo "  no $DATASET.agent_events (register Path A, LAB-GUIDE 2.1.2)"
  have trajectory_sessions || echo "  no $DATASET.trajectory_sessions (load the corpus)"
fi
