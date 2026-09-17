#!/bin/sh
# Runs from /docker-entrypoint.d before nginx starts. The gate value is not in
# the image; it must arrive as SITE_TOKEN (see labs/deploy_site.sh).
: "${SITE_TOKEN:?set SITE_TOKEN (the lab-guide gate token) on the container}"
