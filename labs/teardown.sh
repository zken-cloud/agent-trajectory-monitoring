#!/usr/bin/env bash
# Tear down the expensive parts. Data survives by default.
#
#   bash labs/teardown.sh            # runtime only  - keeps BigQuery + Spanner
#   bash labs/teardown.sh --all      # everything, including the corpus
#
# WHY RUNTIME-ONLY IS THE DEFAULT (WORKSHOP-PLAN.md 8.3)
# GKE + Agent Engine are ~73% of the weekly cost and hold nothing anyone wants
# to keep - the corpus, findings and graph are the artifacts of the day. The
# real risk is not the week, it is the month nobody is watching: left running,
# this is ~$255/month.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
P="${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
REGION="${REGION:-us-central1}"
ALL=0; [ "${1:-}" = "--all" ] && ALL=1

echo "== Agent Engine deployments"
# Deployed by `adk deploy`, NOT by Terraform, so terraform destroy leaves them
# running and billing. They are the single most-forgotten resource here.
#
# REST, not gcloud: there is no `gcloud ai reasoning-engines` verb in current
# gcloud (`Invalid choice: 'reasoning-engines'`), so the CLI cannot list or
# delete them at all.
API="https://${REGION}-aiplatform.googleapis.com/v1"
TOKEN="$(gcloud auth print-access-token)"
engines=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$API/projects/$P/locations/$REGION/reasoningEngines" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(e["name"] for e in d.get("reasoningEngines",[])))')
if [ -z "$engines" ]; then
  echo "  none"
else
  for re in $engines; do
    echo "  deleting $re"
    # force=true also removes child sessions; without it the delete 409s.
    curl -sf -X DELETE -H "Authorization: Bearer $TOKEN" \
      "$API/${re}?force=true" >/dev/null && echo "    ok"
  done
fi

echo "== GKE (terraform, runtime module only)"
terraform -chdir=terraform destroy -auto-approve -input=false \
  -var "project_id=$P" -target=module.agent_runtime 2>&1 | tail -3

if [ "$ALL" = "0" ]; then
  echo
  echo "KEPT: BigQuery datasets and the Spanner instance - the artifacts of the day."
  echo "      Spanner is ~\$0.123/hr (~\$21/week). Re-run with --all to remove it."
  exit 0
fi

echo "== Everything else (terraform)"
terraform -chdir=terraform destroy -auto-approve -input=false -var "project_id=$P" 2>&1 | tail -3

# Built out of band for the real-corpus graph comparison, so Terraform does not
# know it exists and destroy will not touch it. Left behind, it is the kind of
# resource nobody finds until the bill.
echo "== Unmanaged leftovers"
gcloud spanner databases delete trajectory_real --instance=trajectory-graph \
  --project="$P" --quiet 2>/dev/null && echo "  dropped spanner db trajectory_real" || true
bq --project_id="$P" rm -r -f -d "${P}:trajectory_real" 2>/dev/null \
  && echo "  dropped bq dataset trajectory_real" || true
echo "done"
