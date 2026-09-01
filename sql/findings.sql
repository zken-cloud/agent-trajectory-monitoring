-- Unified findings table. Every layer writes here; Looker Studio and the
-- enterprise export (Wiz) read here. One schema is what makes fusion possible.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET}.findings` (
  finding_id     STRING,
  rule_id        STRING,
  layer          INT64,        -- 1 rules | 2 anomaly | 3 judgment
  session_id     STRING,
  invocation_id  STRING,
  tool_call_id   STRING,
  agent_version  STRING,
  severity       STRING,       -- low | medium | high | critical
  score          FLOAT64,      -- layer-native score, 0-1 normalised
  detail         STRING,
  detected_at    TIMESTAMP
)
PARTITION BY DATE(detected_at)
CLUSTER BY session_id, rule_id;
