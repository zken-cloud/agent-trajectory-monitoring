-- Trajectory graph schema for Spanner Graph.
--
-- Spanner Graph is CREATE PROPERTY GRAPH over ORDINARY RELATIONAL TABLES. The
-- same rows serve GQL and SQL - no second copy, no separate graph ETL to keep
-- in sync. That property is most of why this is deliverable at a customer
-- rather than a demo.
--
-- PHYSICAL DESIGN (portable lesson, not a workshop shortcut):
-- Every trajectory query is session-scoped, so Invocation interleaves in
-- Session and LlmCall/ToolCall interleave in Invocation. Interleaving co-locates
-- a whole trajectory in one split, which is what makes the scoped taint query
-- cheap. Globally-scoped dimensions (Tool, DataAsset, ExternalEndpoint) stay
-- standalone - they are shared across every session.

-- ===== NODE TABLES: global dimensions =======================================
CREATE TABLE Agent (
  agent_name    STRING(128) NOT NULL,
  agent_version STRING(32)  NOT NULL,
  runs_as       STRING(256),
) PRIMARY KEY (agent_name, agent_version);

CREATE TABLE Principal (
  principal_id  STRING(256) NOT NULL,
  kind          STRING(32),        -- end_user | service_account
) PRIMARY KEY (principal_id);

-- Tool properties come from the manifest. Detections and graph queries bind to
-- THESE, never to tool names: add a seventh tool and the queries still work.
CREATE TABLE Tool (
  tool_name       STRING(128) NOT NULL,
  sensitivity     STRING(32),
  is_egress       BOOL,
  is_mutating     BOOL,
  trust_label     STRING(32),
  off_manifest    BOOL,
) PRIMARY KEY (tool_name);

CREATE TABLE DataAsset (
  asset_id     STRING(256) NOT NULL,
  asset_kind   STRING(64),          -- customer | kb_doc | order | host
  trust_label  STRING(32),          -- TRUSTED | UNTRUSTED  <- taint source marker
) PRIMARY KEY (asset_id);

CREATE TABLE ExternalEndpoint (
  endpoint_id  STRING(512) NOT NULL,
  domain       STRING(256),
  allowlisted  BOOL,
) PRIMARY KEY (endpoint_id);

-- ===== NODE TABLES: the trajectory, interleaved =============================
CREATE TABLE Session (
  session_id     STRING(64) NOT NULL,
  user_id        STRING(256),
  agent_name     STRING(128),
  agent_version  STRING(32),
  started_at     TIMESTAMP,
  ended_at       TIMESTAMP,
  turn_count     INT64,
  outcome        STRING(32),
  flagged        BOOL,              -- set by Layer 1: the funnel's entry point
) PRIMARY KEY (session_id);

CREATE TABLE Invocation (
  session_id     STRING(64) NOT NULL,
  invocation_id  STRING(64) NOT NULL,
  turn_index     INT64,
  user_message   STRING(MAX),
  started_at     TIMESTAMP,
) PRIMARY KEY (session_id, invocation_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE LlmCall (
  session_id            STRING(64) NOT NULL,
  invocation_id         STRING(64) NOT NULL,
  llm_call_id           STRING(64) NOT NULL,
  model                 STRING(128),
  ts                    TIMESTAMP,
  reasoning_summary     STRING(MAX),
  finish_reason         STRING(64),
  context_has_untrusted BOOL,
) PRIMARY KEY (session_id, invocation_id, llm_call_id),
  INTERLEAVE IN PARENT Invocation ON DELETE CASCADE;

CREATE TABLE ToolCall (
  session_id       STRING(64) NOT NULL,
  invocation_id    STRING(64) NOT NULL,
  tool_call_id     STRING(64) NOT NULL,
  llm_call_id      STRING(64),
  tool_name        STRING(128),
  ts               TIMESTAMP,
  status           STRING(32),      -- ok | error | blocked
  sensitivity      STRING(32),
  is_egress        BOOL,
  args_redacted    JSON,
  enforcement_rule STRING(64),
) PRIMARY KEY (session_id, invocation_id, tool_call_id),
  INTERLEAVE IN PARENT Invocation ON DELETE CASCADE;

-- ===== EDGE TABLES ==========================================================
CREATE TABLE Started (
  principal_id STRING(256) NOT NULL,
  session_id   STRING(64)  NOT NULL,
) PRIMARY KEY (principal_id, session_id);

CREATE TABLE RunsAs (
  agent_name    STRING(128) NOT NULL,
  agent_version STRING(32)  NOT NULL,
  principal_id  STRING(256) NOT NULL,
) PRIMARY KEY (agent_name, agent_version, principal_id);

CREATE TABLE HasInvocation (
  session_id    STRING(64) NOT NULL,
  invocation_id STRING(64) NOT NULL,
) PRIMARY KEY (session_id, invocation_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE Planned (
  session_id    STRING(64) NOT NULL,
  invocation_id STRING(64) NOT NULL,
  llm_call_id   STRING(64) NOT NULL,
) PRIMARY KEY (session_id, invocation_id, llm_call_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE Decided (
  session_id    STRING(64) NOT NULL,
  invocation_id STRING(64) NOT NULL,
  llm_call_id   STRING(64) NOT NULL,
  tool_call_id  STRING(64) NOT NULL,
) PRIMARY KEY (session_id, invocation_id, llm_call_id, tool_call_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE OfTool (
  session_id    STRING(64)  NOT NULL,
  invocation_id STRING(64)  NOT NULL,
  tool_call_id  STRING(64)  NOT NULL,
  tool_name     STRING(128) NOT NULL,
) PRIMARY KEY (session_id, invocation_id, tool_call_id, tool_name),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE ReadAsset (
  session_id    STRING(64)  NOT NULL,
  invocation_id STRING(64)  NOT NULL,
  tool_call_id  STRING(64)  NOT NULL,
  asset_id      STRING(256) NOT NULL,
) PRIMARY KEY (session_id, invocation_id, tool_call_id, asset_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

CREATE TABLE WroteTo (
  session_id    STRING(64)  NOT NULL,
  invocation_id STRING(64)  NOT NULL,
  tool_call_id  STRING(64)  NOT NULL,
  endpoint_id   STRING(512) NOT NULL,
) PRIMARY KEY (session_id, invocation_id, tool_call_id, endpoint_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

-- THE LOAD-BEARING EDGE. Derived, not observed: built by joining tool results to
-- the LLM calls whose CONTEXT WINDOW contained them, which is exactly what the
-- before_model callback captured. Attendees build this edge themselves in
-- Lab 2.3 - it is the difference between a pretty picture and a security graph.
CREATE TABLE FlowedInto (
  session_id       STRING(64)  NOT NULL,
  invocation_id    STRING(64)  NOT NULL,
  asset_id         STRING(256) NOT NULL,
  llm_call_id      STRING(64)  NOT NULL,
  via_tool_call_id STRING(64),
  trust_label      STRING(32),
) PRIMARY KEY (session_id, invocation_id, asset_id, llm_call_id),
  INTERLEAVE IN PARENT Session ON DELETE CASCADE;

-- ===== INDEXES: the taint query's entry points ==============================
-- Tiny tables, but this is where variable-length path search STARTS. Without
-- these the traversal begins with a full scan of every untrusted asset.
CREATE INDEX ToolByEgress   ON Tool (is_egress);
CREATE INDEX AssetByTrust   ON DataAsset (trust_label);
CREATE INDEX SessionFlagged ON Session (flagged, started_at DESC);

-- ===== THE PROPERTY GRAPH ===================================================
CREATE PROPERTY GRAPH TrajectoryGraph
  NODE TABLES (
    Agent            KEY (agent_name, agent_version),
    Principal        KEY (principal_id),
    Session          KEY (session_id),
    Invocation       KEY (session_id, invocation_id),
    LlmCall          KEY (session_id, invocation_id, llm_call_id),
    ToolCall         KEY (session_id, invocation_id, tool_call_id),
    Tool             KEY (tool_name),
    DataAsset        KEY (asset_id),
    ExternalEndpoint KEY (endpoint_id)
  )
  EDGE TABLES (
    Started KEY (principal_id, session_id)
      SOURCE KEY (principal_id) REFERENCES Principal (principal_id)
      DESTINATION KEY (session_id) REFERENCES Session (session_id)
      LABEL STARTED,

    RunsAs KEY (agent_name, agent_version, principal_id)
      SOURCE KEY (agent_name, agent_version) REFERENCES Agent (agent_name, agent_version)
      DESTINATION KEY (principal_id) REFERENCES Principal (principal_id)
      LABEL RUNS_AS,

    HasInvocation KEY (session_id, invocation_id)
      SOURCE KEY (session_id) REFERENCES Session (session_id)
      DESTINATION KEY (session_id, invocation_id) REFERENCES Invocation (session_id, invocation_id)
      LABEL HAS,

    Planned KEY (session_id, invocation_id, llm_call_id)
      SOURCE KEY (session_id, invocation_id) REFERENCES Invocation (session_id, invocation_id)
      DESTINATION KEY (session_id, invocation_id, llm_call_id)
        REFERENCES LlmCall (session_id, invocation_id, llm_call_id)
      LABEL PLANNED,

    Decided KEY (session_id, invocation_id, llm_call_id, tool_call_id)
      SOURCE KEY (session_id, invocation_id, llm_call_id)
        REFERENCES LlmCall (session_id, invocation_id, llm_call_id)
      DESTINATION KEY (session_id, invocation_id, tool_call_id)
        REFERENCES ToolCall (session_id, invocation_id, tool_call_id)
      LABEL DECIDED,

    OfTool KEY (session_id, invocation_id, tool_call_id, tool_name)
      SOURCE KEY (session_id, invocation_id, tool_call_id)
        REFERENCES ToolCall (session_id, invocation_id, tool_call_id)
      DESTINATION KEY (tool_name) REFERENCES Tool (tool_name)
      LABEL OF_TOOL,

    ReadAsset KEY (session_id, invocation_id, tool_call_id, asset_id)
      SOURCE KEY (session_id, invocation_id, tool_call_id)
        REFERENCES ToolCall (session_id, invocation_id, tool_call_id)
      DESTINATION KEY (asset_id) REFERENCES DataAsset (asset_id)
      LABEL READ,

    WroteTo KEY (session_id, invocation_id, tool_call_id, endpoint_id)
      SOURCE KEY (session_id, invocation_id, tool_call_id)
        REFERENCES ToolCall (session_id, invocation_id, tool_call_id)
      DESTINATION KEY (endpoint_id) REFERENCES ExternalEndpoint (endpoint_id)
      LABEL WROTE_TO,

    FlowedInto KEY (session_id, invocation_id, asset_id, llm_call_id)
      SOURCE KEY (asset_id) REFERENCES DataAsset (asset_id)
      DESTINATION KEY (session_id, invocation_id, llm_call_id)
        REFERENCES LlmCall (session_id, invocation_id, llm_call_id)
      LABEL FLOWED_INTO
  );
