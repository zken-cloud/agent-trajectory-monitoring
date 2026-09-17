#!/usr/bin/env bash
# Rebuild the lab guide site from LAB-GUIDE.md and redeploy it.
#
# There was no script for this and the site sat three weeks behind the guide,
# serving a workshop that no longer existed. Rebuild and deploy are one step
# here for exactly that reason - build.py output is not committed, so a deploy
# without a rebuild silently ships whatever was last built locally.
#
#   SITE_PROJECT=<PROJECT_ID> SITE_HOST=<SITE_HOST> SITE_TOKEN=<gate-token> \
#     bash labs/deploy_site.sh
#
# ############################################################################
# THE --ingress FLAG IS A SECURITY CONTROL, NOT A PREFERENCE.
#
# This service grants roles/run.invoker to allUsers. That is only safe because
# ingress is restricted to the load balancer: the run.app URL returns 404 from
# the internet, and every real request arrives through the LB, where IAP
# enforces. `gcloud run deploy` defaults ingress to `all`, so a deploy that
# omits this flag publishes the whole guide publicly on the run.app URL and
# bypasses IAP completely - with no error and nothing visibly different on the
# custom domain. Never remove it.
# ############################################################################
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
: "${SITE_PROJECT:?set SITE_PROJECT}"
: "${SITE_HOST:?set SITE_HOST}"
: "${SITE_TOKEN:?set SITE_TOKEN}"
PROJECT="$SITE_PROJECT"
REGION="${SITE_REGION:-us-central1}"
SERVICE="${SITE_SERVICE:-trajectory-site}"
HOST="$SITE_HOST"

echo "== Rebuild"
python3 site/build.py

echo "== Deploy"
gcloud run deploy "$SERVICE" --source site/ \
  --project="$PROJECT" --region="$REGION" \
  --ingress=internal-and-cloud-load-balancing \
  --set-env-vars="SITE_TOKEN=$SITE_TOKEN" \
  --quiet

echo "== Verify"
ing=$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
        --format="value(metadata.annotations.'run.googleapis.com/ingress')")
[ "$ing" = "internal-and-cloud-load-balancing" ] \
  && echo "  ingress   $ing" \
  || { echo "  FAIL ingress is '$ing' - the run.app URL is PUBLIC, fix before sharing"; exit 1; }

# 404 is the pass condition: allUsers can invoke, but ingress refuses anything
# that did not come through the load balancer.
code=$(curl -s -o /dev/null -w '%{http_code}' \
   "$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
       --format='value(status.url)')/")
[ "$code" = "404" ] && echo "  run.app   404 (correct - not reachable directly)" \
                    || echo "  WARNING run.app returned $code, expected 404"

# IAP lags the API by 1-5 minutes after a change; a 200 here right after a
# deploy is usually that lag, not a missing policy. Re-check before debugging.
code=$(curl -s -o /dev/null -w '%{http_code}' "https://$HOST/")
[ "$code" = "302" ] && echo "  $HOST  302 to IAP (correct)" \
                    || echo "  WARNING $HOST returned $code, expected 302"
