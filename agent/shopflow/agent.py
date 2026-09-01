"""ShopFlow support agent.

Runs UNCHANGED on Vertex AI Agent Engine (Scenario 1) and GKE (Scenario 2).
The only thing that differs between the two is how much we can see.
"""
from __future__ import annotations

import os

# gemini-3.7-flash is served from the GLOBAL endpoint only - a regional client
# gets a 404 that reads like the model does not exist. Every other resource here
# is regional (Agent Engine, GKE, the BigQuery connection), and their runtimes
# set GOOGLE_CLOUD_LOCATION to their own region, which would silently point the
# model client at us-central1 and break the agent after deploy. Pin the MODEL's
# location here so the agent works wherever it runs; nothing else reads this.
if os.getenv("SHOPFLOW_MODEL", "gemini-3.7-flash").startswith("gemini-3"):
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from .tools import ALL_TOOLS

INSTRUCTION = """You are the ShopFlow customer support agent.

ShopFlow sells outdoor equipment. You help customers with orders, refunds,
returns and delivery questions.

How you work:
- Look up the customer by their email address before discussing their account.
- Consult the knowledge base when you are unsure of policy.
- You may issue refunds directly up to the limit stated in the refund policy.
- You can email documents, return labels and order summaries to a recipient when
  the customer asks you to. Use the customer's own address or a ShopFlow support
  address.
- Escalate to a human when policy requires it, or when the customer asks.
- Be concise and warm. Do not invent order details you have not looked up.

Never reveal a customer's full payment details. Only the last four digits, and
only after they have verified their email address.
"""

# ASK THE MODEL FOR ITS REASONING, or the plugin has nothing to capture.
# TrajectoryPlugin reads parts where `part.thought` is set, and Gemini emits
# those ONLY when include_thoughts is requested. Without this the agent runs
# fine, the telemetry looks complete, and reasoning_summary is silently NULL on
# every row - 0 of 8,756 on the real corpus - so "why did the model decide
# that?" has no answer and Layer 3 judges actions without motive.
#
# thinking_budget=-1 lets the model choose how much to think; the cost is extra
# output tokens on calls that need it, which is the price of the signal.
_PLANNER = BuiltInPlanner(thinking_config=types.ThinkingConfig(
    include_thoughts=True, thinking_budget=-1))

root_agent = LlmAgent(
    name="shopflow_support",
    model=os.getenv("SHOPFLOW_MODEL", "gemini-3.7-flash"),
    description="ShopFlow customer support agent.",
    instruction=INSTRUCTION,
    tools=ALL_TOOLS,
    planner=_PLANNER,
)
