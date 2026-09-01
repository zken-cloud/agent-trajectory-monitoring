"""TrajectoryPlugin - the ADK reference adapter to the schema contract.

One-line registration:

    from agent_trajectory import TrajectoryPlugin
    runner = Runner(agent=root_agent, plugins=[TrajectoryPlugin.from_env()], ...)

Deliberate structure: everything that touches ADK types lives in the `_extract_*`
helpers below. Everything else builds schema records. Porting to another
framework replaces the extractors only - see ARCHITECTURE.md 4.1.
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from . import manifest as _manifest
from .enforcement import BLOCK, SHADOW, Enforcer
from .schema import (InvocationRecord, LlmCallRecord, SessionRecord,
                     ToolCallRecord, _hash)
from .sinks import Sink, from_env

_MAX_SUMMARY = 800


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# --- ADK-specific extraction. Replace these to port. -------------------------

def _extract_context_tool_results(llm_request: Any) -> list[tuple[str, str]]:
    """(function_call_id, tool_name) for every tool result in the context window.

    THE TAINT-CRITICAL EXTRACTION. Without it the FLOWED_INTO graph edge cannot
    be derived and Lab 2.3 has nothing to investigate.

    DO NOT trust function_response.id here. ADK generates client-side call ids
    (adk-<uuid>) but STRIPS them from the request before sending it to Gemini,
    which rejects them - so by the time before_model sees the contents, every
    id is None. It is populated in a scripted test double, which is exactly how
    this stayed invisible: the scripted corpus had 8086/8756 llm calls with
    context ids, the real one had 0, and FLOWED_INTO derived zero edges on real
    traffic. Correlate by (tool_name, ordinal) against the plugin's own
    execution log instead - order is authoritative, ids are not.
    """
    found: list[tuple[str, str]] = []
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            fr = getattr(part, "function_response", None)
            if fr is not None:
                found.append((getattr(fr, "id", None) or "", getattr(fr, "name", "") or ""))
    return found


def _resolve_context_ids(found: list[tuple[str, str]],
                         seq: list[tuple[str, str]]) -> list[str]:
    """Fill in missing call ids by matching the Nth result of a tool to the
    Nth execution of that tool in this session. Both lists are chronological."""
    by_name: dict[str, list[str]] = {}
    for name, call_id in seq:
        by_name.setdefault(name, []).append(call_id)
    used: dict[str, int] = {}
    out: list[str] = []
    for call_id, name in found:
        if call_id:                      # a provider that does supply ids
            out.append(call_id)
            continue
        k = used.get(name, 0)
        used[name] = k + 1
        candidates = by_name.get(name, [])
        if k < len(candidates):
            out.append(candidates[k])
    return out


def _extract_decided_calls(llm_response: Any) -> list[tuple[str, str, dict]]:
    """(function_call_id, tool_name, args) the model just decided to make."""
    out: list[tuple[str, str, dict]] = []
    content = getattr(llm_response, "content", None)
    for part in getattr(content, "parts", None) or []:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            out.append((getattr(fc, "id", None) or "",
                        getattr(fc, "name", "") or "",
                        dict(getattr(fc, "args", None) or {})))
    return out


def _extract_reasoning(llm_response: Any) -> tuple[str | None, str | None]:
    """(reasoning, response_text) - TWO streams, deliberately not merged.

    Reasoning is what the model thought; response_text is what the user was
    actually told. Merging them hides response-channel disclosure: in a
    Crescendo (A6) the agent leaks data it legitimately read, across turns, in
    its replies - never calling an egress tool, so every tool-level rule stays
    silent. If the judge cannot see replies it cannot see the attack.

    We still do NOT ship raw chain-of-thought here: it is high-volume and
    routinely contains the PII the agent just read. Capped summary plus hash;
    the verbatim record lives in the platform's GEAP logging table, which has
    its own IAM and retention (ARCHITECTURE.md 4.2).
    """
    thoughts: list[str] = []
    replies: list[str] = []
    content = getattr(llm_response, "content", None)
    for part in getattr(content, "parts", None) or []:
        text = getattr(part, "text", None)
        # part.thought is a BOOLEAN FLAG marking this part as reasoning - the
        # words live in part.text like any other part. Recording str(part.thought)
        # stored the literal "True" as the model's chain of thought, which looks
        # captured and says nothing.
        #
        # The branch matters just as much: a thought part also carries text, so
        # letting it fall through recorded the model's PRIVATE REASONING as the
        # reply the customer received. That is the exact conflation this
        # function was split apart to prevent, and it would have made the judge
        # believe the agent said its deliberation out loud.
        if getattr(part, "thought", None):
            if text:
                thoughts.append(text)
            continue
        if text:
            replies.append(text)
    reasoning = " ".join(t.strip() for t in thoughts)[:_MAX_SUMMARY] or None
    response = " ".join(r.strip() for r in replies)[:_MAX_SUMMARY] or None
    return reasoning, response


def _redact(args: dict) -> dict:
    """Keep shape and policy-relevant values; drop free text that may carry PII."""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if len(v) <= 120 else f"<str:{len(v)}:{_hash(v)[:8]}>"
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


# --- the plugin --------------------------------------------------------------

class TrajectoryPlugin(BasePlugin):

    def __init__(self, manifest_path: str, sink: Sink | None = None,
                 enforcement_mode: str | None = None, name: str = "trajectory"):
        super().__init__(name=name)
        self.manifest = _manifest.load(manifest_path)
        self.sink = sink or from_env()
        self.enforcer = Enforcer(self.manifest, enforcement_mode)
        self._llm_calls: dict[str, str] = {}      # function_call_id -> llm_call_id
        self._pending_llm: dict[str, LlmCallRecord] = {}   # invocation_id -> record
        self._tool_starts: dict[str, ToolCallRecord] = {}
        self._open_invocations: dict[str, InvocationRecord] = {}
        self._last_response: dict[str, str] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._turns: dict[str, int] = {}
        # session_id -> [(tool_name, tool_call_id)] in execution order. This is
        # the ONLY reliable way to correlate a tool result back to its call:
        # see _extract_context_tool_results.
        self._tool_seq: dict[str, list[tuple[str, str]]] = {}
        # invocation_id -> [(tool_name, llm_call_id)] the model just decided,
        # in decision order. Consumed by before_tool to rebuild the DECIDED
        # edge, because the model's function_call carries no id either.
        self._decided: dict[str, list[tuple[str, str]]] = {}

    @classmethod
    def from_env(cls) -> "TrajectoryPlugin":
        return cls(
            manifest_path=os.getenv("TRAJECTORY_MANIFEST", "config/tool_manifest.yaml"),
            enforcement_mode=os.getenv("TRAJECTORY_ENFORCEMENT"),
        )

    # -- session / invocation lifecycle --------------------------------------
    async def before_run_callback(self, *, invocation_context):
        sess = invocation_context.session
        sid = sess.id
        if sid not in self._sessions:
            self._sessions[sid] = SessionRecord(
                session_id=sid, app_name=sess.app_name, user_id=sess.user_id,
                agent_name=self.manifest.agent_name,
                agent_version=self.manifest.agent_version,
                baseline_version=self.manifest.agent_version,
                started_at=_now(),
            )
        self._turns[sid] = self._turns.get(sid, 0) + 1
        user_text = _content_text(invocation_context.user_content)
        # Held, not emitted: the row is written in after_run so it can carry
        # final_response. A row emitted at start would need a second write to
        # complete, and streaming inserts do not update.
        self._open_invocations[invocation_context.invocation_id] = InvocationRecord(
            invocation_id=invocation_context.invocation_id, session_id=sid,
            user_id=sess.user_id, agent_name=self.manifest.agent_name,
            agent_version=self.manifest.agent_version,
            turn_index=self._turns[sid], started_at=_now(),
            user_message=user_text[:2000] if user_text else None,
            user_message_hash=_hash(user_text) if user_text else None,
            status="running",
        )
        return None

    async def after_run_callback(self, *, invocation_context):
        inv = invocation_context.invocation_id
        self._pending_llm.pop(inv, None)
        rec = self._open_invocations.pop(inv, None)
        if rec is not None:
            rec.ended_at = _now()
            rec.status = "ok"
            rec.final_response = self._last_response.pop(inv, None)
            self._decided.pop(inv, None)
            self.sink.emit(rec)
        return None

    def close_session(self, session_id: str, outcome: str | None = None) -> None:
        """Emit the multi-turn outcome - the fourth required signal.

        Called by the harness (traffic generator / attack runner) because only
        the caller knows whether the conversation actually ended.
        """
        rec = self._sessions.pop(session_id, None)
        if rec is None:
            return
        state = self.enforcer.state(session_id)
        rec.ended_at = _now()
        rec.turn_count = self._turns.get(session_id, 0)
        rec.outcome = outcome or ("escalated" if state.approved_by_human else "resolved")
        self._tool_seq.pop(session_id, None)
        self.sink.emit(rec)

    def _pop_decision(self, invocation_id: str, tool_name: str) -> str | None:
        """First undelivered decision for this tool in this invocation."""
        pending = self._decided.get(invocation_id)
        if not pending:
            return None
        for i, (name, llm_call_id) in enumerate(pending):
            if name == tool_name:
                pending.pop(i)
                return llm_call_id
        return None

    # -- model hooks ----------------------------------------------------------
    async def before_model_callback(self, *, callback_context, llm_request):
        inv = callback_context.invocation_id
        ctx_results = _extract_context_tool_results(llm_request)
        trust = [self.manifest.tool(name).trust_label for _, name in ctx_results]
        ctx_ids = _resolve_context_ids(
            ctx_results, self._tool_seq.get(_session_id(callback_context), []))
        rec = LlmCallRecord(
            llm_call_id=f"llm-{uuid.uuid4().hex[:16]}",
            invocation_id=inv,
            session_id=_session_id(callback_context),
            agent_name=self.manifest.agent_name,
            agent_version=self.manifest.agent_version,
            model=str(getattr(llm_request, "model", "") or ""),
            ts=_now(),
            context_tool_call_ids=ctx_ids,
            context_trust_labels=trust,
            context_has_untrusted=any(t == _manifest.UNTRUSTED for t in trust),
        )
        self._pending_llm[inv] = rec
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        inv = callback_context.invocation_id
        rec = self._pending_llm.pop(inv, None)
        if rec is None:
            return None
        reasoning, response = _extract_reasoning(llm_response)
        rec.reasoning_summary = reasoning
        rec.reasoning_hash = _hash(reasoning) if reasoning else None
        rec.response_text = response
        rec.response_hash = _hash(response) if response else None
        if response:
            self._last_response[inv] = response
        rec.finish_reason = str(getattr(llm_response, "finish_reason", "") or "") or None
        # Gemini returns no function_call.id, and ADK assigns its own only AFTER
        # this callback - so fc_id is empty in production and the DECIDED edge
        # (LlmCall -> ToolCall) cannot be keyed on it. Measured on the real
        # corpus: 0 of 3869 tool calls had llm_call_id, against 5009 of 5009 on
        # the scripted one. Queue the decisions by name instead; before_tool
        # pops them in order.
        for fc_id, name, _args in _extract_decided_calls(llm_response):
            if fc_id:
                rec.decided_tool_calls.append(fc_id)
                self._llm_calls[fc_id] = rec.llm_call_id
            self._decided.setdefault(inv, []).append((name, rec.llm_call_id))
        self.sink.emit(rec)
        return None

    # -- tool hooks: telemetry AND enforcement --------------------------------
    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        session_id = _session_id(tool_context)
        pol = self.manifest.tool(tool.name)
        fc_id = getattr(tool_context, "function_call_id", None) or f"tc-{uuid.uuid4().hex[:16]}"

        rec = ToolCallRecord(
            tool_call_id=fc_id,
            invocation_id=tool_context.invocation_id,
            session_id=session_id,
            agent_name=self.manifest.agent_name,
            agent_version=self.manifest.agent_version,
            tool_name=tool.name,
            ts=_now(),
            llm_call_id=self._llm_calls.get(fc_id) or self._pop_decision(
                tool_context.invocation_id, tool.name),
            args_redacted=_redact(tool_args),
            args_hash=_hash(tool_args),
            sensitivity=pol.sensitivity,
            is_egress=pol.is_egress,
            is_mutating=pol.is_mutating,
            result_trust_label=pol.trust_label,
            off_manifest=not self.manifest.is_known(tool.name),
        )

        # ---- ENFORCEMENT (Lab 2.6) -----------------------------------------
        verdict = self.enforcer.evaluate(tool.name, tool_args, session_id)
        if verdict is not None:
            rec.enforcement_rule = verdict.rule_id
            rec.enforcement_action = verdict.action
            if verdict.action != SHADOW:
                rec.status = "blocked"
                rec.ended_at = _now()
                self.enforcer.record(tool.name, tool_args, session_id, blocked=True)
                self.sink.emit(rec)
                # Returning a dict short-circuits the tool: it never executes.
                return verdict.to_tool_result()

        self.enforcer.record(tool.name, tool_args, session_id, blocked=False)
        self._tool_starts[fc_id] = rec
        self._tool_seq.setdefault(session_id, []).append((tool.name, fc_id))
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        fc_id = getattr(tool_context, "function_call_id", None) or ""
        rec = self._tool_starts.pop(fc_id, None)
        if rec is None:
            return None
        rec.ended_at = _now()
        rec.status = "error" if _is_error(result) else "ok"
        if rec.status == "error":
            rec.error_class = str(result.get("error", ""))[:120] if isinstance(result, dict) else "unknown"

        # graph edge material
        if isinstance(result, dict):
            assets = result.get("_data_assets") or []
            rec.data_assets_read = [str(a) for a in assets][:50]
        if rec.is_egress:
            target = tool_args.get("to") or tool_args.get("recipient") or tool_args.get("url")
            if target:
                rec.external_endpoints_written = [str(target)]
        if self.manifest.tool(tool.name).is_sensitive_read and rec.status == "ok":
            self.enforcer.note_sensitive_read(rec.session_id, rec.tool_call_id)

        self.sink.emit(rec)
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        fc_id = getattr(tool_context, "function_call_id", None) or ""
        rec = self._tool_starts.pop(fc_id, None)
        if rec is not None:
            rec.status = "error"
            rec.error_class = type(error).__name__
            rec.ended_at = _now()
            self.sink.emit(rec)
        return None

    def flush(self) -> None:
        self.sink.flush()


# --- small helpers -----------------------------------------------------------

def _session_id(ctx: Any) -> str:
    try:
        return ctx.get_invocation_context().session.id
    except Exception:
        return getattr(ctx, "invocation_id", "unknown")


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    return " ".join(p.text for p in (getattr(content, "parts", None) or [])
                    if getattr(p, "text", None))


def _is_error(result: Any) -> bool:
    return isinstance(result, dict) and (
        result.get("status") in ("error", "blocked") or "error" in result
    )
