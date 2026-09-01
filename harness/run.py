"""Shared conversation harness.

Used by both the red-team suite and the benign traffic generator so attack and
benign trajectories are produced by IDENTICAL machinery. If they were generated
differently, the generator itself would become the strongest signal and the
Layer 2 models would learn that instead of the attacks (WORKSHOP-PLAN 2.3).
"""
from __future__ import annotations

import os
import pathlib
import sys
import uuid
from typing import AsyncGenerator, Iterable

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_trajectory import TrajectoryPlugin
from agent_trajectory.sinks import JsonlSink, from_env

MANIFEST = str(ROOT / "config" / "tool_manifest.yaml")


def make_plugin(enforcement: str | None = None, out_dir: str | None = None):
    sink = JsonlSink(out_dir) if out_dir else from_env()
    return TrajectoryPlugin(MANIFEST, sink=sink,
                            enforcement_mode=enforcement or os.getenv("TRAJECTORY_ENFORCEMENT"))


def make_analytics_plugin():
    """PATH A - the native ADK BigQuery Agent Analytics plugin.

    This is the 80%, and it is Google's code, not ours. Registering it creates
    an `agent_events` table plus ~20 flat per-event-type views
    (v_llm_request, v_tool_completed, v_agent_transfer, ...) with the identity
    headers already unnested, and it streams through the BigQuery Storage Write
    API rather than the insert path our own sink uses.

    Two things it gives us that we would otherwise hand-roll:

      trace_id      Also carried by Cloud Trace, so the harness plane and the
                    platform plane join on it. This is the spine of the whole
                    telemetry picture and it is why enable_otel_correlation is
                    forced True here - the plugin defaults it to FALSE, and
                    with it off there is no join key and the planes stay
                    separate piles of rows.
      tool provenance  LOCAL | MCP | SUB_AGENT | A2A | TRANSFER_AGENT | ...
                    Supersedes ToolCallRecord.origin, which we populated by
                    hand and could only ever set to LOCAL.

    What it does NOT give us is `context_tool_call_ids` - which prior tool
    results were in the model's context window. Nothing native captures that,
    and without it the FLOWED_INTO taint edge cannot be derived. That single
    field is the security 20% and the reason TrajectoryPlugin still exists.

    HOW THE TWO PLANES JOIN - measured on ADK 2.6.3, not assumed:

      session_id, invocation_id   Present and populated on BOTH planes. This
                                  is the join, and the only one.
      trace_id                    Populated on 100% of native events; also
                                  carried by Cloud Trace. Joins the harness
                                  plane to the platform plane.
      function_call_id            DO NOT JOIN ON THIS. The native view exposes
                                  the column (from attributes.adk.function_call_id)
                                  but it is NULL - verified on BOTH a scripted
                                  model and a real gemini-3.7-flash run, where
                                  `attributes.adk` held only app_name and
                                  schema_version. Our tool_call_id therefore
                                  has no native counterpart, and a per-tool-call
                                  join has to go through
                                  (session_id, invocation_id, tool_name) plus
                                  ordering. Re-check this when ADK upgrades.

    Already native, so do NOT re-instrument them in our own schema:
      content.usage.{prompt,completion,total}   token counts
      latency_ms.{time_to_first_token_ms,total_ms}
      content.tool_origin                       LOCAL | MCP | SUB_AGENT | ...

    Returns None when the environment is not configured, so offline runs
    (run_all.sh, the catch-up scripts, CI) are unaffected and need no GCP.
    """
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    dataset = os.getenv("ADK_ANALYTICS_DATASET")
    if not (project and dataset):
        return None
    from google.adk.plugins.bigquery_agent_analytics_plugin import (
        BigQueryAgentAnalyticsPlugin, BigQueryLoggerConfig)
    return BigQueryAgentAnalyticsPlugin(
        project_id=project,
        dataset_id=dataset,
        location=os.getenv("ADK_ANALYTICS_LOCATION", "US"),
        config=BigQueryLoggerConfig(
            create_views=True,
            enable_otel_correlation=True,
            # Same reasoning as reasoning_summary in our own schema: raw
            # payloads routinely carry the PII the agent just read, and this
            # table is not the place for it.
            max_content_length=int(os.getenv("ADK_ANALYTICS_MAX_CONTENT", "4000")),
        ),
    )


class ScriptedLlm(BaseLlm):
    """Deterministic model for offline runs (catch-up scripts, CI).

    `plan` is an ordered list of model TURNS: a (tool_name, args) tuple emits a
    function call, a string emits a reply to the user.

    Step is counted as the number of model turns already in the context, NOT the
    number of tool results. Counting tool results cannot advance a conversation
    that has no tool calls - which is exactly A6, where the agent makes one
    lookup and then discloses across four further replies.
    """
    model: str = "scripted"
    plan: list = []

    async def generate_content_async(self, llm_request, stream=False
                                     ) -> AsyncGenerator[LlmResponse, None]:
        step = sum(1 for c in (llm_request.contents or [])
                   if getattr(c, "role", None) == "model")
        item = self.plan[step] if step < len(self.plan) else None
        if isinstance(item, tuple):
            name, args = item
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                function_call=types.FunctionCall(
                    id=f"fc-{uuid.uuid4().hex[:10]}", name=name, args=args))]))
            return
        text = item if isinstance(item, str) else "Is there anything else I can help with?"
        yield LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text=text)]))


async def run_conversation(turns: Iterable[str], *, plugin, model=None,
                           plan: list | None = None, user_id: str = "u",
                           app_name: str = "shopflow", outcome: str | None = None
                           ) -> str:
    """Run a multi-turn conversation and return the session id."""
    from shopflow.agent import root_agent

    if plan is not None:
        root_agent.model = ScriptedLlm(plan=plan)
    elif model:
        root_agent.model = model

    # Path A FIRST, then Path B. Ordering is load-bearing: ADK stops at the
    # first plugin whose before_tool returns a value, and TrajectoryPlugin
    # returns one when enforcement blocks a call. Analytics ahead of it means
    # a blocked attempt is still recorded - blocked outcomes are telemetry
    # (LAB-GUIDE 2.6.4), and a tool that never appears is indistinguishable
    # from a tool that was stopped.
    analytics = make_analytics_plugin()
    plugins = [p for p in (analytics, plugin) if p is not None]
    runner = InMemoryRunner(agent=root_agent, app_name=app_name, plugins=plugins)
    session = await runner.session_service.create_session(
        app_name=app_name, user_id=user_id)
    for turn in turns:
        msg = types.Content(role="user", parts=[types.Part(text=turn)])
        async for _ in runner.run_async(user_id=user_id, session_id=session.id,
                                        new_message=msg):
            pass
    plugin.close_session(session.id, outcome)
    if analytics is not None:
        await analytics.flush()   # batched async writer; a script exits before it drains
    return session.id
