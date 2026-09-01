#!/usr/bin/env bash
# Run THREE DAYS BEFORE the workshop, not on the day.
# 40 independent Argolis projects means 40 chances an org policy blocks something;
# discovering that at 09:20 with 39 people waiting is the worst outcome available.
set -uo pipefail
P="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
L="${GOOGLE_CLOUD_LOCATION:-us-central1}"
fail=0
ok(){ printf '  [ OK ] %s\n' "$1"; }
no(){ printf '  [FAIL] %s\n' "$1"; fail=1; }
echo "preflight: project=$P region=$L"

echo "APIs:"
# cloudbuild is required by `adk deploy gke` (it builds and pushes the image).
# Missing it fails late and confusingly, as a permission denial on gcr.io.
for api in aiplatform bigquery spanner container pubsub artifactregistry cloudtrace cloudbuild; do
  gcloud services list --enabled --project "$P" 2>/dev/null | grep -q "^$api" \
    && ok "$api.googleapis.com" || no "$api.googleapis.com not enabled"
done

echo "Quota / access:"
gcloud spanner instance-configs list --project "$P" >/dev/null 2>&1 \
  && ok "Spanner reachable" || no "Spanner not reachable"
gcloud container get-server-config --location "$L" --project "$P" >/dev/null 2>&1 \
  && ok "GKE reachable in $L" || no "GKE not reachable in $L"
bq --project_id="$P" query --use_legacy_sql=false --dry_run 'SELECT 1' >/dev/null 2>&1 \
  && ok "BigQuery query permitted" || no "BigQuery query denied"

echo "Spanner edition (Spanner Graph needs ENTERPRISE):"
# CREATE PROPERTY GRAPH is rejected on STANDARD edition - the graph lab cannot
# run at all. Verified live 2026-08-10. This is a hard blocker, not a cost knob.
if gcloud spanner instances create pf-edition-probe --project "$P" \
     --config="regional-$L" --description=preflight --processing-units=100 \
     --edition=ENTERPRISE >/dev/null 2>&1; then
  ok "can create ENTERPRISE Spanner instance"
  gcloud spanner instances delete pf-edition-probe --project "$P" --quiet >/dev/null 2>&1
else
  no "cannot create ENTERPRISE Spanner instance - Spanner Graph will not work"
fi

echo "Vertex models:"
TOKEN="$(gcloud auth print-access-token 2>/dev/null)"
probe_model() {  # $1=model $2=location
  local url host
  if [ "$2" = "global" ]; then host="aiplatform.googleapis.com"; else host="$2-aiplatform.googleapis.com"; fi
  url="https://$host/v1/projects/$P/locations/$2/publishers/google/models/$1:generateContent"
  [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $TOKEN" \
       -H 'Content-Type: application/json' "$url" \
       -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}')" = "200" ]
}
# The agent model lives at `global`, not in a region: gemini-3.6-flash returns
# 404 from us-central1. Probe it where it actually runs.
probe_model gemini-3.6-flash global \
  && ok "gemini-3.6-flash at global (agent + red team + external judge)" \
  || no "gemini-3.6-flash not available at global"

# ...and the in-BigQuery judge separately, because AI.GENERATE_BOOL can only
# reach REGIONAL models. A global-only model fails there with "project does not
# have access to it", which reads like an IAM problem and is not one.
probe_model gemini-2.5-flash "$L" \
  && ok "gemini-2.5-flash in $L (in-BigQuery judge)" \
  || no "gemini-2.5-flash not available in $L"
# 3.1-pro is published at GLOBAL only; a regional BigQuery connection cannot
# reach it, so it is used by labs/judge_external.py rather than AI.GENERATE_BOOL.
probe_model gemini-3.1-pro-preview global \
  && ok "gemini-3.1-pro-preview at global (optional higher-precision judge)" \
  || echo "  [WARN] gemini-3.1-pro-preview unavailable - fall back to the in-BigQuery judge"

echo "Org policy:"
# A real public-object read, not an unauthenticated bucket LIST (which 401s
# regardless of policy and produced a false FAIL in an earlier version).
curl -sf -r 0-64 -o /dev/null https://storage.googleapis.com/gcp-public-data-landsat/index.csv.gz \
  && ok "public GCS object read allowed (T2 corpus bucket)" \
  || no "public GCS read blocked - corpus bucket unreachable"
echo "  [MANUAL] Looker Studio: can you open and COPY an externally-shared report?"

echo; [ $fail -eq 0 ] && echo "PREFLIGHT PASS" || echo "PREFLIGHT FAIL - report before the workshop"
exit $fail
