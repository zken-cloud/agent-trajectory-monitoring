-- THE JUDGE'S EVALUATED POPULATION, materialised before the judge runs.
--
-- Layers 1 and 2 see every session. Layer 3 sees a sample, because a model call
-- per trajectory is the detection budget (WORKSHOP-PLAN 9.1: 40 attendees x 300
-- trajectories in a 20-minute window is ~600 calls/min across the room).
--
-- That difference used to be invisible. Precision and recall for L3 were
-- computed against the whole corpus while the judge had only looked at a few
-- hundred sessions, and the dashboard printed the result next to L1's numbers
-- as though they were measured the same way. Recording WHAT WAS JUDGED is what
-- makes the two comparable - v_detection_quality reads this table for the L3
-- denominator and reports `basis` so nobody has to guess.
--
-- Destination: ${DATASET}.judge_coverage (replaced each run).
WITH flagged AS (
  -- Everything Layers 1 and 2 already flagged. `layer < 3` matters: without it
  -- a second run picks up the judge's OWN findings and the sample grows every
  -- time, which quietly changes the denominator between runs.
  SELECT DISTINCT session_id, 'flagged' AS basis
  FROM `${PROJECT_ID}.${DATASET}.findings`
  WHERE layer < 3
),
risk_surface AS (
  -- The A6 shape: touched sensitive data, never called an egress tool, and ran
  -- long. No rule fires on it, so without this branch the judge never sees the
  -- one attack class only it can catch.
  SELECT s.session_id, 'risk_surface' AS basis
  FROM `${PROJECT_ID}.${DATASET}.trajectory_sessions` s
  JOIN `${PROJECT_ID}.${DATASET}.trajectory_tool_calls` t USING (session_id)
  GROUP BY s.session_id
  HAVING COUNTIF(t.sensitivity IN ('pii','financial')) >= 1
     AND COUNTIF(t.is_egress) = 0
     AND ANY_VALUE(s.turn_count) >= ${RISK_MIN_TURNS}
  -- session_id is a DETERMINISTIC TIEBREAKER, not decoration. Ordering by
  -- turn_count alone leaves ties arbitrary, and a LIMIT over an arbitrary order
  -- silently dropped A6 from the sample on the first run - it ranks 38th of 318
  -- once the order is stable.
  ORDER BY ANY_VALUE(s.turn_count) DESC, s.session_id
  LIMIT ${JUDGE_RISK_SAMPLE}
),
random_benign AS (
  -- Unflagged sessions, so precision has a denominator that is not all suspects.
  SELECT session_id, 'random' AS basis
  FROM `${PROJECT_ID}.${DATASET}.trajectory_sessions`
  WHERE session_id NOT IN (
    SELECT DISTINCT session_id FROM `${PROJECT_ID}.${DATASET}.findings` WHERE layer < 3)
  ORDER BY FARM_FINGERPRINT(session_id)
  LIMIT ${JUDGE_SAMPLE}
)
SELECT session_id,
       -- 'flagged' sorts first, so MIN prefers it when a session qualifies twice.
       MIN(basis)          AS basis,
       CURRENT_TIMESTAMP() AS judged_at
FROM (SELECT * FROM flagged
      UNION ALL SELECT * FROM risk_surface
      UNION ALL SELECT * FROM random_benign)
GROUP BY session_id;
