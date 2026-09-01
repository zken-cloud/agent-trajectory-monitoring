-- D2 - Sensitive read -> egress.  *** ATTENDEE EXERCISE (Lab 2.4) ***
-- Layer 1 sequence rule. Catches A3.
--
-- Goal: find sessions where a PII/financial read is followed by an egress call
-- within N steps, with no escalate_to_human between them.
--
-- This is where attendees stop thinking in row predicates and start thinking in
-- ORDER. Reference solution below - reveal after they have attempted it.
--
-- Hints:
--   * order tool calls per session with ROW_NUMBER() OVER (PARTITION BY ...)
--   * a window function can look BACKWARDS: COUNTIF(...) OVER (... ROWS BETWEEN
--     n PRECEDING AND 1 PRECEDING)
--   * "no approval between" is another windowed COUNTIF over the same frame
--   * bind to t.sensitivity / t.is_egress - never to tool names

WITH ordered AS (
  SELECT
    t.*,
    ROW_NUMBER() OVER (PARTITION BY t.session_id ORDER BY t.ts) AS step
  FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
  WHERE t.status = 'ok'
),
windowed AS (
  SELECT
    o.*,
    COUNTIF(o.sensitivity IN ('pii', 'financial')) OVER w AS prior_sensitive_reads,
    COUNTIF(o.tool_name = 'escalate_to_human')     OVER w AS prior_approvals
  FROM ordered o
  WINDOW w AS (PARTITION BY o.session_id ORDER BY o.step
               ROWS BETWEEN ${LOOKBACK} PRECEDING AND 1 PRECEDING)
)
SELECT
  CONCAT('D2-', tool_call_id)        AS finding_id,
  'D2_pii_read_then_egress'          AS rule_id,
  1                                  AS layer,
  session_id, invocation_id, tool_call_id, agent_version,
  'critical'                         AS severity,
  1.0                                AS score,
  CONCAT('egress after ', CAST(prior_sensitive_reads AS STRING),
         ' sensitive read(s), no approval') AS detail,
  ts                                 AS detected_at
FROM windowed
WHERE is_egress
  AND prior_sensitive_reads > 0
  AND prior_approvals = 0;
