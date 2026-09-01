-- Layer 2, second technique: BQML KMEANS on trajectory SHAPE.
--
-- Promoted from a callout to a labbed technique after goal drift was measured
-- and rejected (see goal_drift_REJECTED.sql). This is also the day's only
-- CREATE MODEL, so it carries the BQML coverage.
--
-- MEASURED, and the reason it earns its place - surprisal and KMEANS catch
-- COMPLETELY DISJOINT attacks on the shipped corpus:
--
--     attack   KMEANS   surprisal
--     A1        -          -
--     A2        -          -
--     A3       YES         -          <- unusual shape: PII read + egress, 2 calls
--     A4        -         YES         <- unusual order: repeated refunds
--     A5        -         YES         <- unusual order: repeated diagnostics
--     A6        -          -
--
--   Zero overlap. Together 3/6; surprisal alone 2/6.
--   KMEANS  @1% contamination: 15 benign false positives in 2,000.
--
-- Surprisal models ORDER - "has this agent ever gone A->B?"
-- KMEANS models SHAPE   - "does this session's profile look like the population?"
-- A session using only ordinary transitions but making 9 PII reads where the
-- population makes 1 is invisible to surprisal and obvious to KMEANS.
--
-- Train on benign traffic only: a model fitted on data containing the attacks
-- learns the attacks as normal.

CREATE OR REPLACE MODEL `${PROJECT_ID}.${DATASET}.trajectory_shape`
OPTIONS (model_type = 'KMEANS', num_clusters = 8, standardize_features = TRUE) AS
SELECT
  COUNT(*)                                          AS n_tool_calls,
  COUNT(DISTINCT tool_name)                         AS n_distinct_tools,
  COUNTIF(sensitivity IN ('pii','financial'))       AS n_sensitive_reads,
  COUNTIF(is_egress)                                AS n_egress,
  COUNTIF(is_mutating)                              AS n_mutating,
  SAFE_DIVIDE(COUNTIF(status = 'error'), COUNT(*))  AS error_rate,
  TIMESTAMP_DIFF(MAX(ts), MIN(ts), SECOND)          AS wall_clock_s
FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls`
WHERE session_id NOT IN (SELECT DISTINCT session_id FROM `${PROJECT_ID}.${DATASET}.findings`)
GROUP BY session_id;
