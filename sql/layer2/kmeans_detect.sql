-- Layer 2 - score sessions against the shape model. Run after kmeans_shape.sql.
WITH feats AS (
  SELECT session_id,
    COUNT(*) AS n_tool_calls, COUNT(DISTINCT tool_name) AS n_distinct_tools,
    COUNTIF(sensitivity IN ('pii','financial')) AS n_sensitive_reads,
    COUNTIF(is_egress) AS n_egress, COUNTIF(is_mutating) AS n_mutating,
    SAFE_DIVIDE(COUNTIF(status='error'), COUNT(*)) AS error_rate,
    TIMESTAMP_DIFF(MAX(ts), MIN(ts), SECOND) AS wall_clock_s
  FROM `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` GROUP BY session_id)
SELECT
  CONCAT('L2K-', session_id)  AS finding_id,
  'L2_shape_anomaly'          AS rule_id,
  2                           AS layer,
  session_id,
  CAST(NULL AS STRING)        AS invocation_id,
  CAST(NULL AS STRING)        AS tool_call_id,
  CAST(NULL AS STRING)        AS agent_version,
  'medium'                    AS severity,
  0.6                         AS score,
  CONCAT('trajectory shape anomaly: ', CAST(n_tool_calls AS STRING), ' calls, ',
         CAST(n_sensitive_reads AS STRING), ' sensitive reads, ',
         CAST(n_egress AS STRING), ' egress') AS detail,
  CURRENT_TIMESTAMP()         AS detected_at
FROM ML.DETECT_ANOMALIES(MODEL `${PROJECT_ID}.${DATASET}.trajectory_shape`,
                         STRUCT(${CONTAMINATION} AS contamination), TABLE feats)
WHERE is_anomaly;
