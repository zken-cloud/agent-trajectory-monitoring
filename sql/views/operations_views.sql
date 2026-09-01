-- THE NON-SECURITY TIER.
--
-- Same four tables, four different questions. Trajectory monitoring is not a
-- security product that happens to store telemetry; it is a telemetry product
-- whose sharpest consumer is security. These views are the rest of the value,
-- and they cost views rather than instrumentation because the signals already
-- land - most of them from the NATIVE plane (ADK BigQuery Agent Analytics),
-- not from anything we wrote.
--
-- WHERE THE NUMBERS COME FROM, and why it matters that you know:
--   tokens   agent_events.content.usage.{prompt,completion,total}   <- NATIVE
--   latency  agent_events.latency_ms.{time_to_first_token_ms,...}   <- NATIVE
--   outcome  trajectory_sessions.outcome                            <- ours
--   policy   trajectory_tool_calls.sensitivity / is_egress          <- ours
-- Do NOT re-instrument the native two. We captured prompt_tokens and
-- latency_ms in our own schema before Path A was wired, and two sources of
-- truth for one number is how they drift.

-- COST -----------------------------------------------------------------------
-- Cost per session, and cost per RESOLVED session, which is the number a
-- platform team actually budgets against. An agent that is cheap per call and
-- resolves nothing is not cheap.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET}.v_session_cost` AS
WITH usage AS (
  SELECT
    session_id,
    SUM(CAST(JSON_VALUE(content, '$.usage.prompt')     AS INT64)) AS prompt_tokens,
    SUM(CAST(JSON_VALUE(content, '$.usage.completion') AS INT64)) AS completion_tokens,
    -- total > prompt + completion whenever the model thought: reasoning tokens
    -- are billed and are NOT in either of the other two. Budget from total.
    SUM(CAST(JSON_VALUE(content, '$.usage.total')      AS INT64)) AS total_tokens,
    COUNT(*)                                                      AS llm_calls
  FROM `${PROJECT_ID}.${DATASET}.agent_events`
  WHERE event_type = 'LLM_RESPONSE'
  GROUP BY session_id
)
SELECT
  s.session_id, s.agent_version, s.outcome, s.turn_count,
  u.llm_calls, u.prompt_tokens, u.completion_tokens, u.total_tokens,
  u.total_tokens - u.prompt_tokens - u.completion_tokens AS reasoning_tokens,
  SAFE_DIVIDE(u.total_tokens, s.turn_count)              AS tokens_per_turn
FROM `${PROJECT_ID}.${DATASET}.trajectory_sessions` s
LEFT JOIN usage u USING (session_id);

-- QUALITY --------------------------------------------------------------------
-- Did the agent do its job? `outcome` is the fourth required signal and until
-- now only the security judge read it. Escalation rate is the one to watch: an
-- agent that escalates everything breaks no policy and delivers no automation,
-- and NO security detection will ever fire on it. That failure is invisible to
-- every other view in this repo.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET}.v_agent_quality` AS
WITH per_session AS (
  SELECT
    s.session_id, s.agent_version, s.outcome, s.turn_count,
    COUNTIF(t.tool_name = 'escalate_to_human') > 0 AS escalated,
    COUNTIF(t.status = 'error')                    AS tool_errors
  FROM `${PROJECT_ID}.${DATASET}.trajectory_sessions` s
  LEFT JOIN `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t USING (session_id)
  GROUP BY s.session_id, s.agent_version, s.outcome, s.turn_count
)
SELECT
  agent_version,
  COUNT(*)                                                  AS sessions,
  COUNTIF(outcome = 'resolved')                             AS resolved,
  COUNTIF(outcome = 'escalated')                            AS escalated_outcome,
  COUNTIF(outcome = 'abandoned')                            AS abandoned,
  COUNTIF(outcome = 'error')                                AS errored,
  SAFE_DIVIDE(COUNTIF(outcome = 'resolved'), COUNT(*))      AS resolution_rate,
  SAFE_DIVIDE(COUNTIF(escalated), COUNT(*))                 AS escalation_rate,
  SAFE_DIVIDE(COUNTIF(tool_errors > 0), COUNT(*))           AS session_error_rate,
  APPROX_QUANTILES(turn_count, 100)[OFFSET(50)]             AS median_turns,
  APPROX_QUANTILES(turn_count, 100)[OFFSET(95)]             AS p95_turns
FROM per_session
GROUP BY agent_version;

-- RELIABILITY ----------------------------------------------------------------
-- Per-tool health, read from the plugin's OWN views rather than re-derived
-- from agent_events. Two reasons: they already unnest the JSON correctly, and
-- the key is `content.tool` - NOT `content.tool_name`, which returns NULL
-- silently and gives you one bogus row with everything collapsed into it.
-- Reading v_tool_completed/v_tool_error means the plugin owns that detail.
--
-- `tool_origin` is the column to notice: LOCAL is a function in your process,
-- MCP and A2A are somebody else's service. A p95 that lives entirely in one
-- A2A tool is a dependency problem, not an agent problem, and you cannot tell
-- those apart without it.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET}.v_tool_health` AS
WITH all_calls AS (
  SELECT tool_name, tool_origin, status, total_ms
  FROM `${PROJECT_ID}.${DATASET}.v_tool_completed`
  UNION ALL
  SELECT tool_name, tool_origin, status, total_ms
  FROM `${PROJECT_ID}.${DATASET}.v_tool_error`
)
SELECT
  tool_name, tool_origin,
  COUNT(*)                                          AS calls,
  COUNTIF(status != 'OK')                           AS errors,
  SAFE_DIVIDE(COUNTIF(status != 'OK'), COUNT(*))    AS error_rate,
  APPROX_QUANTILES(total_ms, 100)[OFFSET(50)]       AS p50_ms,
  APPROX_QUANTILES(total_ms, 100)[OFFSET(95)]       AS p95_ms
FROM all_calls
GROUP BY tool_name, tool_origin;
