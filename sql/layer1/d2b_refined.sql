-- D2b - Sensitive read -> egress, REFINED.  *** ATTENDEE EXERCISE, STEP 2 ***
--
-- Run d2_pii_read_then_egress.sql first and measure it. On the shipped corpus
-- it returns ~231 findings at ~1% precision: it fires on every legitimate
-- "email the customer their return label" session, because that genuinely is a
-- PII read followed by an egress call.
--
-- The rule is not wrong. It is UNDER-SPECIFIED. Egress to an address we own is
-- not exfiltration. Add the destination predicate and re-measure - this is the
-- whole lesson of Lab 2.4, and the number moves in front of them.
WITH verified AS (
  -- Same exception as D1: an address the customer used to identify their
  -- account this session is theirs, and mailing it back is the job. Applying it
  -- in D1 but not here would leave 181 findings for the identical behaviour and
  -- make the two rules disagree about what "untrusted destination" means.
  SELECT DISTINCT session_id,
         LOWER(TRIM(JSON_VALUE(args_redacted, '$.email'))) AS addr
  FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls`
  WHERE tool_name = 'lookup_customer'
    AND JSON_VALUE(args_redacted, '$.email') IS NOT NULL
),
ordered AS (
  SELECT t.*, ROW_NUMBER() OVER (PARTITION BY t.session_id ORDER BY t.ts) AS step
  FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
  WHERE t.status = 'ok'
),
windowed AS (
  SELECT o.*,
    COUNTIF(o.sensitivity IN ('pii','financial')) OVER w AS prior_sensitive_reads,
    COUNTIF(o.tool_name = 'escalate_to_human')    OVER w AS prior_approvals
  FROM ordered o
  WINDOW w AS (PARTITION BY o.session_id ORDER BY o.step
               ROWS BETWEEN ${LOOKBACK} PRECEDING AND 1 PRECEDING)
)
SELECT
  CONCAT('D2b-', w2.tool_call_id) AS finding_id,
  'D2_pii_read_then_egress'       AS rule_id,
  1                               AS layer,
  w2.session_id, w2.invocation_id, w2.tool_call_id, w2.agent_version,
  'critical'                      AS severity,
  1.0                             AS score,
  CONCAT('egress to untrusted destination after ',
         CAST(w2.prior_sensitive_reads AS STRING), ' sensitive read(s)') AS detail,
  w2.ts                           AS detected_at
FROM windowed w2
LEFT JOIN verified v
  ON v.session_id = w2.session_id
 AND v.addr = LOWER(TRIM(JSON_VALUE(w2.args_redacted, '$.to')))
WHERE w2.is_egress
  AND v.addr IS NULL
  AND w2.prior_sensitive_reads > 0
  AND w2.prior_approvals = 0
  -- the added predicate: destination is not one we control
  AND NOT REGEXP_CONTAINS(JSON_VALUE(w2.args_redacted, '$.to'),
                          r'@(${EGRESS_ALLOWLIST})$');
