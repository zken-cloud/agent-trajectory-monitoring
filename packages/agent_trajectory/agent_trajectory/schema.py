"""The telemetry contract.

THIS is the deliverable - not the ADK plugin. Anything that can populate these
four record types feeds the identical graph, detections and dashboards
downstream. `plugin.py` is the ADK reference adapter to this contract; porting
to another framework means writing a new adapter, not a new pipeline.

Four required signals (WORKSHOP-PLAN 4.1):
  model chain-of-thought  -> LlmCallRecord.reasoning_summary
  tool call decision      -> ToolCallRecord.args / justification
  tool call outcome       -> ToolCallRecord.status / data_assets_read
  multi-turn outcome      -> SessionRecord.outcome
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"


def _hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


@dataclass
class SessionRecord:
    session_id: str
    app_name: str
    user_id: str
    agent_name: str
    agent_version: str
    started_at: str
    ended_at: str | None = None
    turn_count: int = 0
    # multi-turn outcome - the fourth required signal
    outcome: str | None = None          # resolved | escalated | abandoned | error
    baseline_version: str | None = None  # scopes Layer 2 scoring (WORKSHOP-PLAN 6.1)
    goal_satisfied: bool | None = None   # filled later by the Layer 3 judge
    TABLE = "trajectory_sessions"


@dataclass
class InvocationRecord:
    invocation_id: str
    session_id: str
    user_id: str
    agent_name: str
    agent_version: str
    turn_index: int
    started_at: str
    ended_at: str | None = None
    user_message: str | None = None
    user_message_hash: str | None = None
    final_response: str | None = None
    status: str = "running"             # running | ok | error
    TABLE = "trajectory_invocations"


@dataclass
class LlmCallRecord:
    llm_call_id: str
    invocation_id: str
    session_id: str
    agent_name: str
    agent_version: str
    model: str
    ts: str
    # --- model chain-of-thought (summarised, never raw - see WORKSHOP-PLAN 4.1) ---
    reasoning_summary: str | None = None
    reasoning_hash: str | None = None
    # --- what the USER WAS ACTUALLY TOLD. A separate stream from reasoning.
    # Conflating the two hides response-channel disclosure (A6): the agent can
    # leak data it legitimately read without ever calling an egress tool, and
    # every tool-level rule stays silent. Verbatim text lives in the platform's
    # GEAP logging table; this is the correlated, capped copy.
    response_text: str | None = None
    response_hash: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    response_tokens: int | None = None
    # --- THE TAINT-CRITICAL FIELDS -------------------------------------------
    # Which prior tool results were in this call's context window. Captured in
    # before_model; without it the FLOWED_INTO graph edge cannot be derived and
    # the graph degrades from a security tool to a picture.
    context_tool_call_ids: list[str] = field(default_factory=list)
    context_trust_labels: list[str] = field(default_factory=list)
    context_has_untrusted: bool = False
    # -------------------------------------------------------------------------
    decided_tool_calls: list[str] = field(default_factory=list)
    TABLE = "trajectory_llm_calls"


@dataclass
class ToolCallRecord:
    tool_call_id: str                   # ADK function_call_id - the join key
    invocation_id: str
    session_id: str
    agent_name: str
    agent_version: str
    tool_name: str
    ts: str
    llm_call_id: str | None = None      # which LLM call DECIDED this
    origin: str = "LOCAL"               # LOCAL | MCP | SUB_AGENT | A2A
    args_redacted: dict[str, Any] = field(default_factory=dict)
    args_hash: str | None = None
    justification: str | None = None    # the model's stated reason
    # --- outcome ---
    status: str = "ok"                  # ok | error | blocked
    error_class: str | None = None
    latency_ms: int | None = None
    ended_at: str | None = None
    # --- manifest properties, denormalised so detections never join to config --
    sensitivity: str = "none"
    is_egress: bool = False
    is_mutating: bool = False
    result_trust_label: str = "TRUSTED"
    off_manifest: bool = False
    # --- graph edge material ---
    data_assets_read: list[str] = field(default_factory=list)
    external_endpoints_written: list[str] = field(default_factory=list)
    # --- enforcement (Lab 2.6) ---
    enforcement_action: str | None = None   # shadow | approve | block
    enforcement_rule: str | None = None
    TABLE = "trajectory_tool_calls"


RECORD_TYPES = (SessionRecord, InvocationRecord, LlmCallRecord, ToolCallRecord)


def to_row(record: Any) -> dict[str, Any]:
    row = {k: v for k, v in asdict(record).items() if not k.isupper()}
    row["schema_version"] = SCHEMA_VERSION
    return row


# --- BigQuery DDL generation -------------------------------------------------
_BQ_TYPES = {
    "str": "STRING", "int": "INT64", "bool": "BOOL", "float": "FLOAT64",
    "list[str]": "ARRAY<STRING>", "dict[str, Any]": "JSON",
}
_TIMESTAMP_COLS = {"ts", "started_at", "ended_at"}


def _bq_type(name: str, annotation: str) -> str:
    base = annotation.replace(" | None", "").strip()
    if name in _TIMESTAMP_COLS:
        return "TIMESTAMP"
    return _BQ_TYPES.get(base, "STRING")


def bigquery_ddl(dataset: str, project: str = "${PROJECT_ID}") -> str:
    """Emit CREATE TABLE statements for the contract. Single source of truth:
    change a dataclass field and the DDL follows."""
    out = [f"-- Generated from agent_trajectory.schema v{SCHEMA_VERSION}. Do not hand-edit.\n"]
    partition = {
        "trajectory_sessions": "started_at", "trajectory_invocations": "started_at",
        "trajectory_llm_calls": "ts", "trajectory_tool_calls": "ts",
    }
    cluster = {
        "trajectory_sessions": "session_id, agent_version",
        "trajectory_invocations": "session_id, invocation_id",
        "trajectory_llm_calls": "session_id, invocation_id",
        "trajectory_tool_calls": "session_id, tool_name",
    }
    for rt in RECORD_TYPES:
        cols = [f"  {n} {_bq_type(n, str(f.type))}"
                for n, f in rt.__dataclass_fields__.items()]
        cols.append("  schema_version STRING")
        out.append(
            f"CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{rt.TABLE}` (\n"
            + ",\n".join(cols)
            + f"\n)\nPARTITION BY DATE({partition[rt.TABLE]})\n"
            f"CLUSTER BY {cluster[rt.TABLE]};\n"
        )
    return "\n".join(out)


def bigquery_schema_json(record_type: Any) -> list[dict[str, str]]:
    """BigQuery table schema as JSON, for Terraform's `schema` argument.

    Same single source of truth as bigquery_ddl: change a dataclass field and
    both the DDL and the Terraform schema follow. Keeping them in sync by hand
    is how the pipeline and the detections silently drift apart.
    """
    out = []
    for name, f in record_type.__dataclass_fields__.items():
        bq = _bq_type(name, str(f.type))
        mode = "REPEATED" if bq.startswith("ARRAY<") else "NULLABLE"
        out.append({"name": name,
                    "type": "STRING" if bq.startswith("ARRAY<") else bq,
                    "mode": mode})
    out.append({"name": "schema_version", "type": "STRING", "mode": "NULLABLE"})
    return out


FINDINGS_SCHEMA = [
    {"name": "finding_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "rule_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "layer", "type": "INT64", "mode": "NULLABLE"},
    {"name": "session_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "invocation_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "tool_call_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "agent_version", "type": "STRING", "mode": "NULLABLE"},
    {"name": "severity", "type": "STRING", "mode": "NULLABLE"},
    {"name": "score", "type": "FLOAT64", "mode": "NULLABLE"},
    {"name": "detail", "type": "STRING", "mode": "NULLABLE"},
    {"name": "detected_at", "type": "TIMESTAMP", "mode": "NULLABLE"},
]


def write_terraform_schemas(out_dir: str) -> None:
    import pathlib
    d = pathlib.Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for rt in RECORD_TYPES:
        (d / f"{rt.TABLE}.json").write_text(
            json.dumps(bigquery_schema_json(rt), indent=2))
    (d / "findings.json").write_text(json.dumps(FINDINGS_SCHEMA, indent=2))
    print(f"wrote {len(RECORD_TYPES) + 1} schema files -> {d}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--terraform":
        write_terraform_schemas(sys.argv[2])
    else:
        print(bigquery_ddl(sys.argv[1] if len(sys.argv) > 1 else "trajectory"))
