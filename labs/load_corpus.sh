#!/usr/bin/env bash
# Load a jsonl corpus into BigQuery.
#
# The deployed agent streams telemetry straight to BigQuery (TRAJECTORY_SINK),
# so the labs never needed this. A corpus generated OFFLINE - traffic/generate.py
# or redteam/attacks.py writing jsonl - had no way in at all.
#
#   bash labs/load_corpus.sh ./corpus_real_37
#   TRAJECTORY_DATASET=trajectory_37 bash labs/load_corpus.sh ./corpus_real_37
#
# REPLACES the table contents by default, because a corpus is a snapshot, not
# an append stream. Running it twice without that quietly doubles every table -
# 1,730 sessions became 3,460 rows over 1,730 distinct session_ids, which does
# not fail, does not look wrong in a row count, and silently halves every
# per-session metric downstream. Pass --append if you really mean to add to
# what is already there.
#
# Tables are created from agent_trajectory.schema.bigquery_ddl(), NOT from a
# schema flag on `bq load`. That matters: bq would infer a plain unpartitioned,
# unclustered table, and a later `terraform apply` would then plan a
# delete/create on tables holding the corpus - changing clustering forces table
# replacement in BigQuery, so the mismatch is data loss rather than a cosmetic
# diff. bigquery_ddl() is the single source of truth the Terraform module is
# kept aligned with (telemetry-pipeline/main.tf, locals.trajectory_tables).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
P="${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
DS="${TRAJECTORY_DATASET:-trajectory}"
CORPUS="${1:?usage: load_corpus.sh <corpus-dir> [--replace]}"
MODE="${2:---replace}"   # --replace (default) | --append

[ -d "$CORPUS" ] || { echo "no such corpus dir: $CORPUS" >&2; exit 1; }
bq --project_id="$P" show "$DS" >/dev/null 2>&1 \
  || bq --project_id="$P" mk --dataset --location=US "$P:$DS"

echo "Creating tables from the schema contract (idempotent):"
# schema.py is loaded BY PATH, not imported as a package: agent_trajectory's
# __init__ pulls in the ADK plugin, and loading a corpus into BigQuery has no
# business requiring the agent framework to be installed.
"${PYTHON:-python3}" -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    'traj_schema', 'packages/agent_trajectory/agent_trajectory/schema.py')
m = importlib.util.module_from_spec(spec)
# Register BEFORE exec: @dataclass resolves annotations via
# sys.modules[cls.__module__], which is None for an unregistered module.
sys.modules['traj_schema'] = m
spec.loader.exec_module(m)
print(m.bigquery_ddl('$DS', '$P'))" \
  | bq --project_id="$P" query --use_legacy_sql=false --format=none

# `findings` is normally created by the Terraform telemetry-pipeline module.
# A dataset made any other way does not have it, and then deploy_views.sh fails
# on v_findings before it reaches anything else. Partition and clustering must
# match the module (main.tf, google_bigquery_table.findings) for the same
# reason as above: a mismatch makes terraform plan a delete/create.
if ! bq --project_id="$P" show "$DS.findings" >/dev/null 2>&1; then
  echo "Creating findings table (Terraform normally does this):"
  bq --project_id="$P" mk --table \
     --time_partitioning_field=detected_at --time_partitioning_type=DAY \
     --clustering_fields=session_id,rule_id \
     "$DS.findings" \
     terraform/modules/telemetry-pipeline/schemas/findings.json
fi

for f in "$CORPUS"/trajectory_*.jsonl; do
  t="$(basename "$f" .jsonl)"
  n="$(wc -l < "$f")"
  printf '  %-28s %6s rows ' "$t" "$n"
  if [ "$MODE" != "--append" ]; then
    bq --project_id="$P" query --use_legacy_sql=false --format=none \
       "DELETE FROM \`$P.$DS.$t\` WHERE TRUE" >/dev/null 2>&1 || true
  fi
  # --ignore_unknown_values so a corpus captured before a field was retired
  # still loads: the extra keys are dropped rather than failing the whole file.
  if bq --project_id="$P" load --source_format=NEWLINE_DELIMITED_JSON \
        --ignore_unknown_values "$DS.$t" "$f" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAILED"; exit 1
  fi
done

bq --project_id="$P" query --use_legacy_sql=false --format=pretty \
 "SELECT COUNT(*) sessions, COUNT(DISTINCT user_id) users,
         MIN(started_at) first_seen, MAX(started_at) last_seen
  FROM \`$P.$DS.trajectory_sessions\`"
