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

    runner = InMemoryRunner(agent=root_agent, app_name=app_name, plugins=[plugin])
    session = await runner.session_service.create_session(
        app_name=app_name, user_id=user_id)
    for turn in turns:
        msg = types.Content(role="user", parts=[types.Part(text=turn)])
        async for _ in runner.run_async(user_id=user_id, session_id=session.id,
                                        new_message=msg):
            pass
    plugin.close_session(session.id, outcome)
    return session.id
