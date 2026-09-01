"""Tool manifest loader.

The manifest is the security seam: detections and enforcement bind to tool
PROPERTIES declared here, never to tool names. Porting this stack to a customer
means writing a new manifest, not rewriting rules.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TRUSTED = "TRUSTED"
UNTRUSTED = "UNTRUSTED"


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    description: str = ""
    sensitivity: str = "none"            # none | low | pii | financial
    is_egress: bool = False
    is_mutating: bool = False
    trust_label: str = TRUSTED           # trust of data this tool RETURNS
    required_scopes: tuple[str, ...] = ()
    limits: dict[str, Any] = field(default_factory=dict)

    @property
    def is_sensitive_read(self) -> bool:
        """Reads data whose exfiltration would matter."""
        return self.sensitivity in ("pii", "financial")

    @property
    def returns_untrusted(self) -> bool:
        """An indirect-prompt-injection ingress point."""
        return self.trust_label == UNTRUSTED


@dataclass(frozen=True)
class Manifest:
    agent_name: str
    agent_version: str
    tools: dict[str, ToolPolicy]
    egress_allowlist_domains: tuple[str, ...] = ()
    enforcement_mode: str = "shadow"
    enforcement_rules: dict[str, str] = field(default_factory=dict)

    def tool(self, name: str) -> ToolPolicy:
        """Unknown tools are not an error - they are finding D5.

        Returning a maximally-suspicious default means an off-manifest tool is
        treated as dangerous rather than silently ignored.
        """
        return self.tools.get(
            name,
            ToolPolicy(name=name, description="OFF-MANIFEST",
                       sensitivity="pii", is_egress=True, is_mutating=True,
                       trust_label=UNTRUSTED),
        )

    def is_known(self, name: str) -> bool:
        return name in self.tools

    def action_for(self, rule_id: str) -> str | None:
        """Enforcement action for a rule id, or None if not enforceable."""
        return self.enforcement_rules.get(rule_id)


def load(path: str | Path) -> Manifest:
    raw = yaml.safe_load(Path(path).read_text())
    tools = {
        name: ToolPolicy(
            name=name,
            description=spec.get("description", ""),
            sensitivity=spec.get("sensitivity", "none"),
            is_egress=bool(spec.get("is_egress", False)),
            is_mutating=bool(spec.get("is_mutating", False)),
            trust_label=spec.get("trust_label", TRUSTED),
            required_scopes=tuple(spec.get("required_scopes", []) or []),
            limits=spec.get("limits", {}) or {},
        )
        for name, spec in (raw.get("tools") or {}).items()
    }
    enf = raw.get("enforcement") or {}
    return Manifest(
        agent_name=(raw.get("agent") or {}).get("name", "unknown"),
        agent_version=(raw.get("agent") or {}).get("version", "0.0.0"),
        tools=tools,
        egress_allowlist_domains=tuple(raw.get("egress_allowlist_domains", []) or []),
        enforcement_mode=enf.get("mode", "shadow"),
        enforcement_rules={r["id"]: r.get("action", "block")
                           for r in (enf.get("rules") or [])},
    )


@functools.lru_cache(maxsize=8)
def load_cached(path: str) -> Manifest:
    return load(path)
