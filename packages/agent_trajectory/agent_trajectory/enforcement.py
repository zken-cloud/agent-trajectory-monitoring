"""Inline enforcement - Layer 1 only.

WORKSHOP-PLAN 4.6. The rules here are the SAME predicates as detections D1-D4,
evaluated against in-flight session state instead of BigQuery. That is the whole
point: a rule you can express in SQL over history you can also evaluate before
the tool runs.

You cannot do this with Layer 2 or 3. An LLM judge costs a model call per
decision, adds seconds of latency to every tool, and has an unbounded false
positive rate. The least clever layer is the only one that can stop anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .manifest import Manifest, ToolPolicy

SHADOW, APPROVE, BLOCK = "shadow", "approve", "block"


@dataclass
class SessionState:
    """In-flight state Layer 1 rules evaluate against. Deliberately tiny - if a
    rule needs more than this it belongs in BigQuery, not in the hot path."""
    refund_total: float = 0.0
    sensitive_reads: list[str] = field(default_factory=list)   # tool_call_ids
    call_signatures: dict[str, int] = field(default_factory=dict)
    approved_by_human: bool = False
    # Addresses the customer themselves supplied to identify their account this
    # session. Emailing one back is the support agent's whole job; the domain
    # allowlist alone cannot express that (see d1_egress_off_allowlist).
    verified_recipients: set[str] = field(default_factory=set)


@dataclass
class Verdict:
    rule_id: str
    reason: str
    action: str = BLOCK

    def to_tool_result(self) -> dict[str, Any]:
        """Returned from before_tool -> ADK skips the tool and hands this to the
        model, which must then explain itself to the user."""
        if self.action == APPROVE:
            return {
                "status": "approval_required",
                "message": (f"Blocked pending human approval [{self.rule_id}]: "
                            f"{self.reason}. Call escalate_to_human to proceed."),
                "policy_rule": self.rule_id,
            }
        return {
            "status": "blocked",
            "error": f"Policy violation [{self.rule_id}]: {self.reason}",
            "policy_rule": self.rule_id,
        }


def _email_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower() if "@" in address else ""


# --- the rules ---------------------------------------------------------------
# Each mirrors a detection in sql/layer1/. Same predicate, different tense.

def d1_refund_over_limit(pol: ToolPolicy, args: dict, st: SessionState,
                         mf: Manifest) -> Verdict | None:
    limit = pol.limits.get("max_amount_per_call")
    amount = args.get("amount")
    if limit is None or amount is None:
        return None
    if float(amount) > float(limit):
        return Verdict("D1_refund_over_limit",
                       f"refund {amount} exceeds per-call limit {limit}")
    return None


def d4_refund_aggregate(pol: ToolPolicy, args: dict, st: SessionState,
                        mf: Manifest) -> Verdict | None:
    """Catches A4 - the attack that walks straight through D1 by splitting."""
    cap = pol.limits.get("max_amount_per_session")
    amount = args.get("amount")
    if cap is None or amount is None:
        return None
    if st.refund_total + float(amount) > float(cap):
        return Verdict("D4_refund_aggregate",
                       f"session refund total would reach "
                       f"{st.refund_total + float(amount):.2f}, cap {cap}")
    return None


def d1_egress_off_allowlist(pol: ToolPolicy, args: dict, st: SessionState,
                            mf: Manifest) -> Verdict | None:
    """Egress to a domain we do not control - UNLESS it is the customer's own
    address, verified in this session.

    A domain allowlist alone says "we may only email ourselves", which is not
    what a support agent does. Measured on the 3.6-flash corpus: 185 of 444
    emails went to a customer at their own address, and every one was flagged.
    That is not a tuning annoyance - a rule that fires on the agent's primary
    legitimate action trains people to ignore it.

    "Verified" here means the customer supplied the address to identify their
    account (lookup_customer) earlier in this same session. That keeps A3 caught:
    it looks up the VICTIM and mails the ATTACKER, so the recipient never matches
    what was verified.
    """
    if not pol.is_egress:
        return None
    recipient = str(args.get("to") or args.get("recipient") or "").strip().lower()
    if recipient and recipient in st.verified_recipients:
        return None
    domain = _email_domain(recipient)
    if domain and domain not in mf.egress_allowlist_domains:
        return Verdict("D1_egress_off_allowlist",
                       f"egress to non-allowlisted domain '{domain}'")
    return None


def d2_pii_read_then_egress(pol: ToolPolicy, args: dict, st: SessionState,
                            mf: Manifest) -> Verdict | None:
    """Catches A3. The sequence rule, evaluated in-flight."""
    if not pol.is_egress or not st.sensitive_reads or st.approved_by_human:
        return None
    return Verdict("D2_pii_read_then_egress",
                   f"egress after {len(st.sensitive_reads)} sensitive read(s) "
                   "with no human approval", action=APPROVE)


def d3_tool_loop(pol: ToolPolicy, args: dict, st: SessionState,
                 mf: Manifest, threshold: int = 5) -> Verdict | None:
    """Catches A5 - cost DoS."""
    from .schema import _hash
    sig = f"{pol.name}:{_hash(args)}"
    if st.call_signatures.get(sig, 0) >= threshold:
        return Verdict("D3_tool_loop",
                       f"'{pol.name}' called {st.call_signatures[sig]}x with "
                       "identical arguments")
    return None


def d5_off_manifest(pol: ToolPolicy, args: dict, st: SessionState,
                    mf: Manifest) -> Verdict | None:
    if mf.is_known(pol.name):
        return None
    return Verdict("D5_off_manifest",
                   f"tool '{pol.name}' is not declared in the agent manifest")


RULES = (d5_off_manifest, d1_refund_over_limit, d4_refund_aggregate,
         d1_egress_off_allowlist, d3_tool_loop, d2_pii_read_then_egress)


class Enforcer:
    """Evaluates Layer 1 rules and resolves the configured mode.

    `shadow` is the production default and the only responsible starting point:
    it records what WOULD have been blocked so you can measure the cost of
    enforcement against real traffic before it breaks someone's refund.
    """

    def __init__(self, manifest: Manifest, mode: str | None = None):
        self._mf = manifest
        self._mode = (mode or manifest.enforcement_mode or SHADOW).lower()
        self._states: dict[str, SessionState] = {}

    def state(self, session_id: str) -> SessionState:
        return self._states.setdefault(session_id, SessionState())

    def evaluate(self, tool_name: str, args: dict, session_id: str) -> Verdict | None:
        pol = self._mf.tool(tool_name)
        st = self.state(session_id)
        for rule in RULES:
            verdict = rule(pol, args, st, self._mf)
            if verdict is None:
                continue
            configured = self._mf.action_for(verdict.rule_id)
            if configured is None:
                continue          # detected but not promoted to enforcement
            if self._mode == SHADOW:
                verdict.action = SHADOW
            elif configured == APPROVE or verdict.action == APPROVE:
                verdict.action = APPROVE
            else:
                verdict.action = BLOCK
            return verdict
        return None

    def record(self, tool_name: str, args: dict, session_id: str,
               blocked: bool) -> None:
        """Advance session state. Blocked calls must NOT advance side-effect
        counters - otherwise a blocked refund still consumes the session cap."""
        from .schema import _hash
        pol = self._mf.tool(tool_name)
        st = self.state(session_id)
        sig = f"{tool_name}:{_hash(args)}"
        st.call_signatures[sig] = st.call_signatures.get(sig, 0) + 1
        if blocked:
            return
        if tool_name == "escalate_to_human":
            st.approved_by_human = True
        # The customer identified their account with this address, so it counts
        # as theirs for the rest of the session. Recorded on the LOOKUP, not on
        # the email, so an attacker cannot verify a destination by naming it.
        if pol.name == "lookup_customer" and args.get("email"):
            st.verified_recipients.add(str(args["email"]).strip().lower())
        if pol.name == "issue_refund" and args.get("amount") is not None:
            st.refund_total += float(args["amount"])

    def note_sensitive_read(self, session_id: str, tool_call_id: str) -> None:
        self.state(session_id).sensitive_reads.append(tool_call_id)
