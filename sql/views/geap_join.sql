-- Correlating native GEAP request-response logs with agent trajectories.
--
-- READ THE LIMITATION FIRST. The GEAP logging table contains NO session_id,
-- trace_id or invocation_id (verified: metadata holds only request_latency;
-- otel_log holds GenAI content records, not trace context; a propagated
-- traceparent header does not survive). So this is NOT an id join.
--
-- Two honest ways to use the table:

-- (1) EVIDENCE LOOKUP - the primary use. Given a session the detections already
--     flagged, pull the verbatim request/response around it. Time + model +
--     user-content match is good enough for a human investigating ONE session,
--     and it is not asked to be unique.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET}.v_geap_evidence` AS
SELECT
  g.logging_time, g.model, g.api_method, g.request_id,
  JSON_VALUE(g.full_request,  '$.contents[0].parts[0].text')       AS first_user_text,
  JSON_VALUE(g.full_response, '$.candidates[0].content.parts[0].text') AS response_text,
  JSON_QUERY(g.full_request,  '$.contents')                        AS full_conversation,
  CAST(JSON_VALUE(g.metadata, '$.request_latency') AS FLOAT64)     AS latency_ms
FROM `${PROJECT_ID}.${DATASET}.geap_request_response` g;

-- (2) CRESCENDO EVIDENCE - the useful trick, and it needs no join at all.
--     Every model call carries the WHOLE conversation so far in
--     full_request.contents, including the agent's own prior replies. So the
--     LAST call of a session contains the entire multi-turn exchange in one
--     row - request and response, both sides, in order.
--
--     That is exactly what a Crescendo attack needs to be read as a whole, and
--     it is why native logging is worth enabling even though it does not join.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET}.v_geap_conversations` AS
WITH sized AS (
  SELECT g.*,
    ARRAY_LENGTH(JSON_QUERY_ARRAY(g.full_request, '$.contents')) AS turn_count,
    JSON_VALUE(g.full_request, '$.contents[0].parts[0].text')    AS opening_text
  FROM `${PROJECT_ID}.${DATASET}.geap_request_response` g
)
SELECT * EXCEPT(rn) FROM (
  SELECT s.*, ROW_NUMBER() OVER (
      PARTITION BY s.opening_text ORDER BY s.turn_count DESC, s.logging_time DESC) AS rn
  FROM sized s
) WHERE rn = 1;      -- the longest context per conversation = the full exchange
