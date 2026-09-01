-- D3 - Tool loop / resource exhaustion.  SHIPPED WORKING.
-- Layer 1. Catches A5. Same signal Scenario 1's managed dashboard surfaced -
-- worth saying out loud that managed observability already had this one.
SELECT
  CONCAT('D3-', t.session_id, '-', t.tool_name)  AS finding_id,
  'D3_tool_loop'                                 AS rule_id,
  1                                              AS layer,
  t.session_id,
  ANY_VALUE(t.invocation_id)                     AS invocation_id,
  ANY_VALUE(t.tool_call_id)                      AS tool_call_id,
  ANY_VALUE(t.agent_version)                     AS agent_version,
  'medium'                                       AS severity,
  LEAST(COUNT(*) / 20.0, 1.0)                    AS score,
  CONCAT(t.tool_name, ' repeated ', CAST(COUNT(*) AS STRING),
         'x with identical arguments')           AS detail,
  MIN(t.ts)                                      AS detected_at
FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
GROUP BY t.session_id, t.tool_name, t.args_hash
HAVING COUNT(*) >= ${LOOP_THRESHOLD};
