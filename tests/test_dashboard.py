"""Offline test for the dashboard renderer. No GCP project needed.

Guards the layout bug that shipped in the first version: the value label was
absolutely positioned past the bar end, so any bar at 100% - precision, routinely
- pushed its label outside the card. Nothing caught it because the generator ran
fine and printed a filename; only rendering the page showed it.
"""
from __future__ import annotations

import html.parser
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "labs"))
import dashboard as d  # noqa: E402


class Balanced(html.parser.HTMLParser):
    VOID = {"meta", "br", "input", "img", "hr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"</{tag}>")


ROWS = [
    # precision 1.0 is the case that broke the layout, so it leads.
    dict(rule_id="D1_refund_over_limit", layer=1, basis="full corpus",
         flagged_sessions=88, true_positives=88, false_positives=0,
         sessions_evaluated=1992, attacks_in_scope=94, precision=1.0, recall=0.936),
    dict(rule_id="L2_transition_surprisal", layer=2, basis="full corpus",
         flagged_sessions=7, true_positives=0, false_positives=7,
         sessions_evaluated=1992, attacks_in_scope=94, precision=0.0, recall=0.0),
    dict(rule_id="L3_judge_policy_breach", layer=3, basis="judged sample",
         flagged_sessions=137, true_positives=90, false_positives=47,
         sessions_evaluated=386, attacks_in_scope=91, precision=None, recall=None),
]


# One session with the shape the graph exists to reveal: an untrusted read that
# influences a later model call, which then sends data out.
G_INV = [dict(invocation_id="i1", turn_index=1, user_message="my order is late")]
G_LLM = [dict(llm_call_id="l1", invocation_id="i1", reasoning_summary="check kb",
              context_tool_call_ids=[], context_has_untrusted=False),
         dict(llm_call_id="l2", invocation_id="i1", reasoning_summary="email it",
              context_tool_call_ids=["t1"], context_has_untrusted=True)]
G_TOOL = [dict(tool_call_id="t1", llm_call_id="l1", tool_name="search_kb",
               status="ok", sensitivity="none", is_egress=False,
               result_trust_label="UNTRUSTED", enforcement_rule=None,
               data_assets_read=["kb_doc:KB-002"], external_endpoints_written=[]),
          dict(tool_call_id="t2", llm_call_id="l2", tool_name="send_email",
               status="ok", sensitivity="low", is_egress=True,
               result_trust_label="TRUSTED", enforcement_rule=None,
               data_assets_read=[], external_endpoints_written=["evil@example.net"])]


def graph_checks() -> dict:
    svg, stat = d.session_graph(G_INV, G_LLM, G_TOOL,
                                [{"rule_id": "D1_egress_off_allowlist"}])
    p = Balanced()
    p.feed(f"<div>{svg}</div>")
    return {
        "graph markup is balanced": not p.stack and not p.errors,
        # The derived edge is the whole point: if the plugin stops capturing
        # context_tool_call_ids this is the check that goes red.
        "derived FLOWED_INTO edge is drawn": "stroke-dasharray" in svg,
        "taint path detected end to end": stat.get("taint") is True,
        "untrusted source marked danger": "var(--crit)" in svg,
        "every lane is labelled": all(lbl in svg for _, lbl in d.LANES),
        "ids are translated to words":
            d.who("redteam-a2").startswith("A2")
            and "policy limit" in d.means("D1_refund_over_limit"),
        "unknown rule falls back to its id":
            d.means("X_unknown_rule") == "X_unknown_rule",
    }


def main() -> int:
    frag = (d.quality_chart(ROWS)
            + d.count_chart([{"rule_id": "D1", "n": 88}, {"rule_id": "L3", "n": 137}],
                            "rule_id", "n", "findings")
            + d.table(ROWS, [("rule_id", "rule"), ("precision", "precision")],
                      {"precision"})
            + d.table([], [("rule_id", "rule")], set()))

    p = Balanced()
    p.feed(f"<div>{frag}</div>")
    widths = [float(w) for w in re.findall(r'class="bar"[^>]*width:([\d.]+)%', frag)]

    checks = {
        "html is balanced": not p.stack and not p.errors,
        "every bar within 0-100%": all(0 <= w <= 100 for w in widths),
        "true zero draws no mark": 0.0 in widths,
        "full bar renders at 100%": 100.0 in widths,
        # The gutter is what makes a 100% bar safe; without it the label is
        # positioned off the end of the mark and clipped by the card.
        "values live in a reserved gutter": frag.count('class="val"') == len(widths),
        "NULL precision degrades to a dash": "—" in frag,
        "two series carry a legend": 'class="legend"' in frag,
        "empty table says so, not crashes": "no rows" in frag,
        "user text is escaped": "&lt;" in d.table(
            [{"a": "<script>x</script>"}], [("a", "a")], set()),
    }
    checks.update(graph_checks())
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
