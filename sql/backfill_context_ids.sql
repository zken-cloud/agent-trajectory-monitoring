-- One-off repair for corpora captured before the id-correlation fix.
--
-- WHY THIS EXISTS
-- Gemini does not return function_call.id. ADK assigns its own (adk-<uuid>) but
-- strips it from the request before the next model call, and only assigns it
-- AFTER after_model_callback runs. The plugin keyed both graph edges on that id,
-- so on real traffic they were empty:
--     context_tool_call_ids  0 of 8756 llm calls   (FLOWED_INTO -> 0 edges)
--     tool_calls.llm_call_id 0 of 3869 tool calls  (DECIDED     -> 0 edges)
-- The scripted corpus set ids explicitly and scored 8086/8756 and 5009/5009,
-- which is why every offline check passed. plugin.py now correlates by
-- (tool_name, ordinal); this backfills data captured before that.
--
-- THIS IS RECONSTRUCTION, NOT INFERENCE. ADK sends the whole session history,
-- so the context window IS every prior tool result in the session. Verified
-- against context_trust_labels, which WAS captured correctly: all 8756 label
-- sequences rebuild identically, and all 3869 tool calls resolve a decider.
--
--   bq query --use_legacy_sql=false < sql/backfill_context_ids.sql

MERGE `${PROJECT_ID}.${DATASET}.trajectory_llm_calls` T
USING (
  SELECT l.llm_call_id,
         IFNULL(ARRAY_AGG(t.tool_call_id IGNORE NULLS ORDER BY t.ts), []) AS ids
  FROM `${PROJECT_ID}.${DATASET}.trajectory_llm_calls` l
  LEFT JOIN `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
    ON t.session_id = l.session_id AND t.ts < l.ts
  GROUP BY l.llm_call_id
) S
ON T.llm_call_id = S.llm_call_id
WHEN MATCHED AND ARRAY_LENGTH(T.context_tool_call_ids) = 0
THEN UPDATE SET context_tool_call_ids = S.ids;

-- The tool call was decided by the most recent LLM call in the same
-- invocation. Invocation-scoped, not session-scoped: a later turn's model call
-- must never claim an earlier turn's tool.
MERGE `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` T
USING (
  SELECT t.tool_call_id,
         ARRAY_AGG(l.llm_call_id ORDER BY l.ts DESC LIMIT 1)[SAFE_OFFSET(0)] AS decider
  FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t
  LEFT JOIN `${PROJECT_ID}.${DATASET}.trajectory_llm_calls` l
    ON l.invocation_id = t.invocation_id AND l.ts < t.ts
  GROUP BY t.tool_call_id
) S
ON T.tool_call_id = S.tool_call_id
WHEN MATCHED AND T.llm_call_id IS NULL
THEN UPDATE SET llm_call_id = S.decider;
