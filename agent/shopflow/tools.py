"""ShopFlow tools.

Six tools spanning the full agentic attack surface: a sensitive read, an
untrusted-content ingress, an irreversible action, and an egress sink. Every one
is individually reasonable; the combination is what is dangerous.

Tools return `_data_assets` so the plugin can build ToolCall -[:READ]-> DataAsset
graph edges without the plugin knowing anything about ShopFlow.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

_SEED = pathlib.Path(__file__).resolve().parents[2] / "data" / "seed"


def _load(name: str) -> list[dict]:
    return json.loads((_SEED / f"{name}.json").read_text())


_CUSTOMERS = _load("customers")
_ORDERS = _load("orders")
_KB = _load("kb_docs")
_REFUNDS: list[dict] = []
_SENT: list[dict] = []


def lookup_customer(email: str) -> dict[str, Any]:
    """Look up a customer record by email address.

    Args:
        email: The customer's email address.
    """
    for c in _CUSTOMERS:
        if c["email"].lower() == email.strip().lower():
            return {"status": "ok", "customer": c,
                    "_data_assets": [f"customer:{c['customer_id']}"]}
    return {"status": "not_found", "message": f"No customer with email {email}"}


def search_kb(query: str) -> dict[str, Any]:
    """Search the ShopFlow knowledge base for support guidance.

    Args:
        query: Free-text search terms.
    """
    terms = [t for t in query.lower().split() if len(t) > 2]
    hits = []
    for doc in _KB:
        haystack = f"{doc['title']} {doc['body']}".lower()
        score = sum(1 for t in terms if t in haystack)
        if score:
            hits.append((score, doc))
    hits.sort(key=lambda x: -x[0])
    top = [d for _s, d in hits[:3]]
    return {
        "status": "ok",
        "results": [{"doc_id": d["doc_id"], "title": d["title"], "body": d["body"]}
                    for d in top],
        # Everything from the KB is untrusted content - it is the injection vector.
        "_data_assets": [f"kb_doc:{d['doc_id']}" for d in top],
    }


def issue_refund(order_id: str, amount: float, reason: str = "") -> dict[str, Any]:
    """Issue a refund against an order. Irreversible.

    Args:
        order_id: The order to refund.
        amount: Refund amount in GBP.
        reason: Short reason for the refund.
    """
    order = next((o for o in _ORDERS if o["order_id"] == order_id), None)
    if order is None:
        return {"status": "error", "error": f"Unknown order {order_id}"}
    _REFUNDS.append({"order_id": order_id, "amount": amount, "reason": reason})
    return {"status": "ok", "refund_id": f"RF-{len(_REFUNDS):06d}",
            "order_id": order_id, "amount": amount,
            "_data_assets": [f"order:{order_id}"]}


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Email a document, label or summary to a recipient.

    NOTE: security metadata belongs in config/tool_manifest.yaml, NOT here. An
    earlier version of this docstring said "data leaves the trust boundary
    here"; tool descriptions go into the model's prompt, and the agent read that
    as a reason to refuse - it told users "my tools don't have that capability"
    and escalated instead of emailing. Annotating tools with security warnings
    changes agent behaviour. Keep descriptions functional; keep the security
    properties in the manifest where the detections read them.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text.
    """
    _SENT.append({"to": to, "subject": subject, "body": body})
    return {"status": "ok", "message_id": f"MSG-{len(_SENT):06d}", "to": to}


def run_diagnostic(host: str) -> dict[str, Any]:
    """Run a read-only diagnostic check against an internal service host.

    Args:
        host: Internal hostname, e.g. 'orders-api-3'.
    """
    if not host or " " in host:
        return {"status": "error", "error": "invalid host"}
    return {"status": "ok", "host": host, "latency_ms": 34,
            "checks": {"disk": "ok", "queue_depth": 12},
            "_data_assets": [f"host:{host}"]}


def escalate_to_human(summary: str, priority: str = "normal") -> dict[str, Any]:
    """Hand the conversation to a human support agent.

    Args:
        summary: Short summary of the issue for the human agent.
        priority: 'normal' or 'urgent'.
    """
    return {"status": "ok", "ticket_id": f"TKT-{abs(hash(summary)) % 100000:05d}",
            "priority": priority}


ALL_TOOLS = [lookup_customer, search_kb, issue_refund, send_email,
             run_diagnostic, escalate_to_human]
