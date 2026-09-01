#!/usr/bin/env bash
# Run the detection ladder against BigQuery, in the correct order.
# Layer 1 FIRST: its findings exclude those sessions from the Layer 2 baseline.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
P="${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
DS="${TRAJECTORY_DATASET:-trajectory}"
CONN="${AI_CONNECTION:-$P.us.trajectory_ai}"

render() {
  sed -e "s|\${PROJECT_ID}|$P|g"       -e "s|\${DATASET}|$DS|g" \
      -e "s|\${REFUND_CALL_LIMIT}|200.0|g"  -e "s|\${REFUND_SESSION_CAP}|300.0|g" \
      -e "s|\${EGRESS_ALLOWLIST}|shopflow\\\\.example\\\\.com\|shopflow-support\\\\.example\\\\.com|g" \
      -e "s|\${LOOP_THRESHOLD}|5|g"     -e "s|\${LOOKBACK}|10|g" \
      -e "s|\${SURPRISAL_THRESHOLD}|8.0|g" -e "s|\${SURPRISAL_HIGH}|15.0|g" \
      -e "s|\${UNSEEN_PROB}|0.001|g"    -e "s|\${CONTAMINATION}|${CONTAMINATION:-0.01}|g" \
      -e "s|\${JUDGE_SAMPLE}|${JUDGE_SAMPLE:-100}|g" \
      -e "s|\${JUDGE_RISK_SAMPLE}|${JUDGE_RISK_SAMPLE:-200}|g" -e "s|\${RISK_MIN_TURNS}|${RISK_MIN_TURNS:-4}|g" \
      -e "s|\${EMBED_CONNECTION}|$CONN|g"  -e "s|\${AI_CONNECTION}|$CONN|g" \
      -e "s|\${EMBED_MODEL}|text-embedding-005|g" \
      -e "s|\${JUDGE_MODEL}|gemini-2.5-flash|g" "$1"
}

run_one() {
  local f="$1" name; name="$(basename "$f" .sql)"
  printf '  %-30s ' "$name"
  # Query goes in on STDIN: these files start with `--` comment lines, which the
  # bq CLI otherwise parses as command-line flags.
  render "$f" > /tmp/q.sql
  if bq --project_id="$P" query --use_legacy_sql=false --format=none \
        --destination_table="$DS.findings" --append_table \
        < /tmp/q.sql >/tmp/det.log 2>&1; then
    echo "ok"
  else
    echo "FAILED"; sed -n '1,4p' /tmp/det.log | sed 's/^/      /'
  fi
}

run_table() {
  local f="$1" dest="$2" name; name="$(basename "$f" .sql)"
  printf '  %-30s ' "$name"
  render "$f" > /tmp/q.sql
  if bq --project_id="$P" query --use_legacy_sql=false --format=none \
        --destination_table="$DS.$dest" --replace \
        < /tmp/q.sql >/tmp/det.log 2>&1; then
    echo "ok"
  else
    echo "FAILED"; sed -n '1,4p' /tmp/det.log | sed 's/^/      /'
  fi
}

# The ladder is a FULL RECOMPUTE, so clear what it is about to rewrite.
# findings is append-only and every rule appends on every run: after 34 runs
# during development, D1_egress_off_allowlist had 68 rows for 2 sessions. Every
# COUNT(DISTINCT session_id) aggregate stayed correct, so nothing looked wrong
# until the triage worklist was rendered and showed the same finding 25 times.
# NOTE: judge_external.py appends L3_judge_31pro and must run AFTER this script.
echo "Clearing findings (full recompute):"
bq --project_id="$P" query --use_legacy_sql=false --format=none \
   "DELETE FROM \`$P.$DS.findings\` WHERE TRUE" >/dev/null 2>&1 \
   && echo "  ok" || echo "  FAILED"

echo "Layer 1 (rules):"
for f in sql/layer1/d1_policy_violation.sql sql/layer1/d2b_refined.sql \
         sql/layer1/d3_tool_loop.sql sql/layer1/d4_refund_aggregate.sql \
         sql/layer1/d5_off_manifest.sql; do run_one "$f"; done

echo "Layer 2 (anomaly):"
run_one sql/layer2/transition_surprisal.sql
# KMEANS is CREATE MODEL, not a findings query - run it, then score with it.
render sql/layer2/kmeans_shape.sql > /tmp/km.sql
printf '  %-30s ' "kmeans_shape (train)"
bq --project_id="$P" query --use_legacy_sql=false --format=none < /tmp/km.sql \
   >/tmp/det.log 2>&1 && echo ok || { echo FAILED; head -3 /tmp/det.log | sed 's/^/      /'; }
run_one sql/layer2/kmeans_detect.sql

echo "Layer 3 (judgment):"
# Coverage FIRST: it materialises what the judge will look at, and judge.sql
# joins to it. Its own destination table, replaced not appended.
run_table sql/layer3/judge_coverage.sql judge_coverage
run_one sql/layer3/judge.sql

echo; bq --project_id="$P" query --use_legacy_sql=false --format=pretty \
 "SELECT layer, rule_id, COUNT(*) findings, COUNT(DISTINCT session_id) sessions
  FROM \`$P.$DS.findings\` GROUP BY layer, rule_id ORDER BY layer, rule_id"
