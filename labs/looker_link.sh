#!/usr/bin/env bash
# Looker Studio links for the detection dashboard.
#
# WHAT THE LINKING API CAN AND CANNOT DO
#   It can create a report and bind data sources to it. It CANNOT build pages
#   or charts - those exist only in a template someone made by hand - and it
#   CANNOT attach several data sources to a blank report: a blank report has a
#   single embedded data source and takes NO alias at all (ds.connector=...,
#   not ds.ds0.connector=...). Aliases like ds0 only exist once a template
#   exists to own them. Passing ds.ds0 with no template fails with
#   "ds0 is not a valid data source alias for this report".
#
# BUILDING THE TEMPLATE (once, by whoever owns the workshop)
#   Run with LOOKER_AUTHOR=1 to print the CREATE link.
#   1. Open the CREATE link. It opens a new report already bound to
#      v_findings_triage. Note c.mode=edit: the API defaults to VIEW mode, and
#      the Resource menu does not exist in view mode.
#   2. Resource > Add data > BigQuery, and add the other three views.
#   3. Build the four pages. Share the report so attendees can copy it.
#   4. Resource > Manage added data sources, and read the Alias column.
#      Those aliases - not necessarily ds0..ds3 - are what the copy link must
#      use. Then:
#        export LOOKER_TEMPLATE_ID=<report id from the report URL>
#        export LOOKER_ALIASES="<alias0> <alias1> <alias2> <alias3>"
#      in the same order as the views below.
set -euo pipefail
P="${GOOGLE_CLOUD_PROJECT:?export GOOGLE_CLOUD_PROJECT}"
DS="${TRAJECTORY_DATASET:-trajectory}"
# The shipped template. Override to point attendees at your own copy.
TEMPLATE_ID="${LOOKER_TEMPLATE_ID:-b86e1f9b-d976-4aa3-94f5-892d09e2cc20}"

views=(v_findings_triage v_fleet_overview v_session_investigator v_detection_quality)
# NO DEFAULT ON PURPOSE. Aliases are assigned by the template, and a guessed
# default emits a URL that looks correct and fails with "dsN is not a valid
# data source alias for this report" - naming a parameter, which sends people
# hunting through connector settings instead of the alias list. Guessed
# ds0..ds3 already cost two rounds of this.
# Aliases belong to a specific template, so the default applies ONLY to the
# shipped one. Point TEMPLATE_ID elsewhere and you must supply your own - a
# guessed ds0..ds3 emits a URL that looks right and fails with "dsN is not a
# valid data source alias for this report", which names a parameter and sends
# people hunting through connector settings instead of the alias list.
SHIPPED_TEMPLATE="b86e1f9b-d976-4aa3-94f5-892d09e2cc20"
default_aliases=""
[ "$TEMPLATE_ID" = "$SHIPPED_TEMPLATE" ] && default_aliases="ds5 ds6 ds8 ds4"
read -r -a aliases <<< "${LOOKER_ALIASES:-$default_aliases}"

# Attendees copy the shared template; they never build one. The CREATE link is
# author-only, so it is off by default - printing it next to the copy link
# invites people to rebuild a dashboard that already exists.
if [ "${LOOKER_AUTHOR:-0}" = "1" ]; then
  echo "CREATE - new report bound to ${views[0]} (template author only):"
  echo "https://lookerstudio.google.com/reporting/create?c.mode=edit&r.reportName=Agent%20Trajectory%20Detection&ds.connector=bigQuery&ds.type=TABLE&ds.projectId=$P&ds.datasetId=$DS&ds.tableId=${views[0]}&ds.billingProjectId=$P"
  echo
fi

if [ -z "$TEMPLATE_ID" ]; then
  echo "COPY - unavailable: export LOOKER_TEMPLATE_ID=<id> once the template exists."
  exit 0
fi
if [ "${#aliases[@]}" -ne "${#views[@]}" ]; then
  echo "COPY - unavailable: need ${#views[@]} data source aliases."
  echo "  Open the template, then Resource > Manage added data sources, and read"
  echo "  the Alias column. The alias is editable there - renaming them to match"
  echo "  the views is worth doing once, so the link survives a source being"
  echo "  added or removed later. Then:"
  echo "    export LOOKER_ALIASES=\"<${views[0]}> <${views[1]}> <${views[2]}> <${views[3]}>\""
  exit 0
fi

params=""
for i in "${!views[@]}"; do
  a="${aliases[$i]}"
  params+="&ds.$a.connector=bigQuery&ds.$a.type=TABLE"
  params+="&ds.$a.projectId=$P&ds.$a.datasetId=$DS&ds.$a.tableId=${views[$i]}"
  params+="&ds.$a.billingProjectId=$P"
done
echo "COPY - copies the template and repoints it at $P.$DS:"
echo "https://lookerstudio.google.com/reporting/create?c.reportId=${TEMPLATE_ID}${params}"
echo
echo "  Add &c.explain=true to either link to see how Looker Studio parsed it."
