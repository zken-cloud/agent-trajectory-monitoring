-- D5 - Off-manifest tool.  SHIPPED WORKING.
-- Layer 1. The agent called something its declared manifest does not contain -
-- a supply-chain / rogue-tool signal. Cheap, and the first thing to break when
-- someone ships a new tool without updating the manifest.
SELECT
  CONCAT('D5-', t.tool_call_id)      AS finding_id,
  'D5_off_manifest'                  AS rule_id,
  1                                  AS layer,
  t.session_id, t.invocation_id, t.tool_call_id, t.agent_version,
  'critical'                         AS severity,
  1.0                                AS score,
  CONCAT('undeclared tool: ', t.tool_name) AS detail,
  t.ts                               AS detected_at
FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
WHERE t.off_manifest;
