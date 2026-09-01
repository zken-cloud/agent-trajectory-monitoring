"""Generate the detection dashboard as one self-contained HTML file.

    python labs/dashboard.py                      # -> dashboard.html
    TRAJECTORY_DATASET=trajectory_real python labs/dashboard.py

WHY THIS EXISTS RATHER THAN A HOSTED APP
The four views are the dashboard; this only renders them. A hosted UI would need
a service, an OAuth client and IAM in 40 separate projects - more moving parts
than the Looker copy link it replaces, not fewer. A generated file needs only the
BigQuery credentials preflight.sh already checks, so it cannot fail on report
sharing, copy permissions, data source aliases, or an org policy that blocks
copying externally-shared reports.

What it gives up: interactivity. No filtering, no drill-down. For reading fixed
numbers in Labs 2.4-2.5 that costs nothing; for working a live queue it would
matter, which is what Looker Studio is still there for (LAB-GUIDE 2.4.1).
"""
from __future__ import annotations

import html
import os
import pathlib
import sys

from google.cloud import bigquery

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
DATASET = os.getenv("TRAJECTORY_DATASET", "trajectory")
OUT = pathlib.Path(os.getenv("DASHBOARD_OUT", "dashboard.html"))

# Palette: reference instance, slots 1 and 2. Validated with the skill's
# validator in BOTH modes - all six checks pass (worst adjacent CVD dE 24.7
# light / 26.8 dark against a >=8 target). Do not hand-pick replacements
# without re-running it.
CSS = """
:root{color-scheme:light;
 --surface:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e;
 --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
 --s1:#2a78d6; --s2:#eb6834; --good:#0ca30c; --crit:#d03b3b;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --surface:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7;
 --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
 --s1:#3987e5; --s2:#d95926;}}
:root[data-theme="dark"]{color-scheme:dark;
 --surface:#1a1a19; --plane:#0d0d0d; --ink:#fff; --ink-2:#c3c2b7;
 --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
 --s1:#3987e5; --s2:#d95926;}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
 font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:32px 24px 64px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:15px;margin:36px 0 4px}
.sub{color:var(--ink-2);margin:0 0 16px;font-size:13px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
 padding:18px 20px;margin-top:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:30px;line-height:1.1;font-weight:600}
.kpi .l{color:var(--ink-2);font-size:12px;margin-top:2px}
.legend{display:flex;gap:16px;align-items:center;margin:2px 0 14px;
 color:var(--ink-2);font-size:12px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;
 vertical-align:middle}
.row{margin-bottom:12px}
.row .name{font-size:12px;color:var(--ink-2);margin-bottom:3px;
 display:flex;justify-content:space-between;gap:12px}
/* The value sits in a RESERVED GUTTER, not floated off the bar end. Absolute
   positioning at left:calc(100% + 6px) reads fine until a bar hits 100% - which
   precision does routinely - and then the label is pushed outside the card and
   clipped. A fixed column cannot clip at any bar width, so there is no
   does-it-fit logic to get wrong. */
.measure{display:flex;align-items:center;gap:8px;margin-bottom:2px}
.track{background:var(--grid);border-radius:4px;height:12px;flex:1}
.bar{height:12px;border-radius:0 4px 4px 0;min-width:0}
.val{width:52px;text-align:right;font-size:11px;color:var(--ink-2);
 font-variant-numeric:tabular-nums;flex:none}
table{border-collapse:collapse;width:100%;font-size:12.5px;
 font-variant-numeric:tabular-nums}
th{text-align:left;color:var(--muted);font-weight:500;padding:6px 10px 6px 0;
 border-bottom:1px solid var(--axis);white-space:nowrap}
td{padding:6px 10px 6px 0;border-bottom:1px solid var(--grid);vertical-align:top}
td.n,th.n{text-align:right}
.tag{font-size:11px;color:var(--ink-2)}
.scroll{overflow-x:auto}
.foot{color:var(--muted);font-size:11.5px;margin-top:28px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.insight{background:var(--surface);border:1px solid var(--ring);border-left:3px solid var(--s1);
 border-radius:0 10px 10px 0;padding:14px 18px;margin-top:12px}
/* Direct child only: a <b> inside the paragraph is emphasis, not a heading,
   and display:block on it broke the sentence across two lines. */
.insight > b{display:block;font-size:15px;margin-bottom:4px}
.insight p{margin:0;color:var(--ink-2);font-size:13px}
.insight.warn{border-left-color:var(--crit)}
.hero{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
 padding:22px 24px;margin-top:12px}
.hero .n{font-size:44px;line-height:1;font-weight:600;letter-spacing:-.5px}
.hero .n small{font-size:15px;font-weight:400;color:var(--ink-2);margin-left:8px}
.hero p{margin:10px 0 0;color:var(--ink-2);font-size:13.5px;max-width:68ch}
.gwrap{overflow-x:auto;background:var(--surface);border:1px solid var(--ring);
 border-radius:10px;padding:16px}
.lane{fill:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}
.nlabel{fill:var(--ink);font-size:11px}
.nsub{fill:var(--ink-2);font-size:9.5px}
.elabel{fill:var(--muted);font-size:9px}
.matrix td.hit{color:var(--good);font-weight:600}
.matrix td.miss{color:var(--muted)}
/* An id is a reference, not a label: keep it available but stop it competing
   with the words that say what actually happened. */
.idsub{color:var(--muted);font-size:11px;font-family:ui-monospace,Menlo,monospace;
 margin-top:2px}
.story{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;
 margin-top:12px}
.step{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
 padding:14px 16px}
.step .k{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
 color:var(--muted);margin-bottom:6px}
.quote{font-style:italic;color:var(--ink)}
.fired{margin:0;padding-left:18px} .fired li{margin-bottom:6px}
#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;
 background:var(--surface);border:1px solid var(--ring);border-radius:6px;
 padding:6px 9px;font-size:12px;box-shadow:0 2px 10px rgba(0,0,0,.12);z-index:9}
"""

JS = """
const tip=document.getElementById('tip');
for(const el of document.querySelectorAll('[data-tip]')){
  el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.style.opacity=1;
    tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';
    tip.style.top=(e.clientY+16)+'px';});
  el.addEventListener('mouseleave',()=>tip.style.opacity=0);
}
"""


def q(client: bigquery.Client, sql: str) -> list[dict]:
    return [dict(r) for r in client.query(sql).result()]


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def bar(frac: float, colour: str, label: str, tip: str) -> str:
    """One horizontal bar: 12px thick, 4px rounded data-end, square at baseline.

    A true zero renders NO mark. Flooring it to a visible sliver would make
    precision 0.000 look like precision 0.04 - the label carries the value, so
    the bar does not need to lie to stay visible.
    """
    w = 0.0 if frac <= 0 else max(0.8, min(100.0, frac * 100))
    return (f'<div class="measure"><div class="track">'
            f'<div class="bar" data-tip="{e(tip)}" '
            f'style="width:{w:.1f}%;background:{colour}"></div></div>'
            f'<div class="val">{e(label)}</div></div>')


def quality_chart(rows: list[dict]) -> str:
    """Precision and recall per rule - TWO series, so a legend is mandatory.

    Both measures are 0-1 on the same scale, which is what makes one shared axis
    honest here. Two measures on different scales would need two charts, never a
    second y-axis.
    """
    out = ['<div class="legend">'
           '<span><i class="sw" style="background:var(--s1)"></i>precision</span>'
           '<span><i class="sw" style="background:var(--s2)"></i>recall</span>'
           '<span style="margin-left:auto">bars are 0–100%</span></div>']
    for r in rows:
        # Keep None as None. Coercing it to 0 before labelling renders "not
        # computable" as "0%", which is a different and much worse claim.
        p, rc = r["precision"], r["recall"]
        out.append(
            f'<div class="row"><div class="name"><span>{e(r["rule_id"])}</span>'
            f'<span class="tag">layer {e(r["layer"])} · {e(r["basis"])} '
            f'· {r["flagged_sessions"]} flagged</span></div>'
            + bar(p or 0, "var(--s1)", pct(p),
                  f'{r["rule_id"]} precision {pct(p)} '
                  f'({r["true_positives"]} of {r["flagged_sessions"]} flagged)')
            + bar(rc or 0, "var(--s2)", pct(rc),
                  f'{r["rule_id"]} recall {pct(rc)} '
                  f'({r["true_positives"]} of {r["attacks_in_scope"]} labelled)')
            + '</div>')
    return "".join(out)


def count_chart(rows: list[dict], key: str, val: str, unit: str) -> str:
    """ONE series, so no legend box - the heading already says what is plotted."""
    top = max([r[val] for r in rows] or [1])
    return "".join(
        f'<div class="row"><div class="name"><span>{e(r[key])}</span></div>'
        + bar(r[val] / top, "var(--s1)", f'{r[val]:,}',
              f'{r[key]}: {r[val]:,} {unit}') + '</div>'
        for r in rows)


# Plain English for every id the page shows. A session hash and a rule_id are
# meaningless to anyone who did not write them, and "self-explanatory" is not a
# nice-to-have on a page whose job is to convince someone this is worth doing.
RULE_MEANING = {
    "D1_refund_over_limit": "refunded more than the policy limit allows",
    "D1_egress_off_allowlist": "emailed data to a domain we do not control",
    "D2_pii_read_then_egress": "read customer PII, then sent it somewhere",
    "D3_tool_loop": "called the same tool over and over",
    "D4_refund_aggregate": "split refunds to stay under the per-call limit",
    "D5_off_manifest": "called a tool that is not in the manifest",
    "L2_transition_surprisal": "took a step that rarely follows what came before",
    "L2_shape_anomaly": "whole-session shape unlike any normal cluster",
    "L3_judge_policy_breach": "a model read the transcript and called it a breach",
    "L3_judge_31pro": "second judge (Gemini 3.1 Pro) agreed it is a breach",
}
ATTACK_NAMES = {
    "a1": "A1 · Direct jailbreak / goal hijack",
    "a2": "A2 · Indirect prompt injection via poisoned KB doc",
    "a3": "A3 · PII exfiltration chain",
    "a4": "A4 · Policy evasion by splitting refunds",
    "a5": "A5 · Tool loop / cost DoS",
    "a6": "A6 · Multi-turn crescendo (response-channel disclosure)",
}


def who(user_id: str | None) -> str:
    """Who was driving this session, in words."""
    u = user_id or ""
    if u.startswith("redteam-"):
        return ATTACK_NAMES.get(u.split("-")[-1].lower(), u)
    if u.startswith("cust-"):
        return f"customer {u[5:]}"
    return u or "unknown"


def means(rule_id: str) -> str:
    return RULE_MEANING.get(rule_id, rule_id)


LANES = [("turn", "USER TURN"), ("llm", "MODEL DECISION"),
         ("tool", "TOOL CALL"), ("data", "DATA TOUCHED")]
LANE_Y = {"turn": 46, "llm": 150, "tool": 254, "data": 358}


def _llm_tip(lc: dict) -> str:
    """Say which channel the text came from, and why it is missing if it is.

    "no reasoning captured" reads like a capture bug. Usually it means the
    agent never asked for reasoning: Gemini only emits thought parts when
    thinking_config.include_thoughts is set, so a model running with the
    default config produces none - 0 of 8,756 rows on the corpus generated
    before the planner was added.
    """
    if lc.get("reasoning_summary"):
        return "THOUGHT: " + lc["reasoning_summary"][:220]
    if lc.get("response_text"):
        return "SAID: " + lc["response_text"][:220]
    return ("no reasoning on this call - the agent ran without "
            "thinking_config.include_thoughts, so the model emitted no thought "
            "parts to capture")


def session_graph(inv: list[dict], llm: list[dict], tools: list[dict],
                  finds: list[dict]) -> tuple[str, dict]:
    """One session as a node-link graph, in swimlanes by telemetry layer.

    This is the shape of the whole solution: four independently captured
    streams, joined by id, with one DERIVED edge (FLOWED_INTO) that no single
    stream contains. A table of tool calls cannot show it - the breach is the
    path, and the path only exists once the layers are connected.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    x = 0.0

    tools_by_llm: dict[str, list[dict]] = {}
    for tc in tools:
        tools_by_llm.setdefault(tc.get("llm_call_id") or "", []).append(tc)
    tool_by_id = {tc["tool_call_id"]: tc for tc in tools}

    # Chronological walk: turn -> model call -> the tools it decided -> data.
    for iv in inv:
        x += 1
        nodes.append(dict(id=f"inv:{iv['invocation_id']}", lane="turn", x=x,
                          label=f"turn {iv['turn_index']}",
                          sub=(iv.get("user_message") or "")[:46],
                          tip=f"USER: {(iv.get('user_message') or '')[:220]}"))
        for lc in [l for l in llm if l["invocation_id"] == iv["invocation_id"]]:
            x += 1
            untrusted = bool(lc.get("context_has_untrusted"))
            nodes.append(dict(id=f"llm:{lc['llm_call_id']}", lane="llm", x=x,
                              label="model call",
                              sub=("saw UNTRUSTED context" if untrusted
                                   else "trusted context only"),
                              danger=untrusted,
                              tip=_llm_tip(lc)))
            edges.append((f"inv:{iv['invocation_id']}",
                          f"llm:{lc['llm_call_id']}", "PLANNED", False))
            # FLOWED_INTO - the derived edge. Every tool result in this call's
            # context contributes its data assets as possible influences.
            for cid in lc.get("context_tool_call_ids") or []:
                src = tool_by_id.get(cid)
                if not src:
                    continue
                for asset in src.get("data_assets_read") or []:
                    edges.append((f"data:{asset}", f"llm:{lc['llm_call_id']}",
                                  "FLOWED_INTO",
                                  src.get("result_trust_label") == "UNTRUSTED"))
            for tc in tools_by_llm.get(lc["llm_call_id"], []):
                x += 1
                blocked = tc.get("status") == "blocked"
                nodes.append(dict(id=f"tool:{tc['tool_call_id']}", lane="tool", x=x,
                                  label=tc["tool_name"],
                                  sub=("BLOCKED " + (tc.get("enforcement_rule") or "")
                                       if blocked else
                                       ("egress" if tc.get("is_egress")
                                        else tc.get("sensitivity") or "ok")),
                                  danger=bool(tc.get("is_egress")) or blocked,
                                  tip=f"{tc['tool_name']} · {tc.get('status')}"
                                      f" · sensitivity={tc.get('sensitivity')}"))
                edges.append((f"llm:{lc['llm_call_id']}",
                              f"tool:{tc['tool_call_id']}", "DECIDED", False))
                for asset in tc.get("data_assets_read") or []:
                    untr = tc.get("result_trust_label") == "UNTRUSTED"
                    if not any(n["id"] == f"data:{asset}" for n in nodes):
                        nodes.append(dict(id=f"data:{asset}", lane="data", x=x,
                                          label=asset.split(":")[0],
                                          sub=asset.split(":")[-1][:22],
                                          danger=untr,
                                          tip=f"{asset} · trust="
                                              f"{tc.get('result_trust_label')}"))
                    edges.append((f"tool:{tc['tool_call_id']}", f"data:{asset}",
                                  "READ", untr))
                for ep in tc.get("external_endpoints_written") or []:
                    if not any(n["id"] == f"data:{ep}" for n in nodes):
                        nodes.append(dict(id=f"data:{ep}", lane="data", x=x,
                                          label="endpoint", sub=ep[:24],
                                          danger=True,
                                          tip=f"data left to {ep}"))
                    edges.append((f"tool:{tc['tool_call_id']}", f"data:{ep}",
                                  "WROTE_TO", True))

    # WINDOW. The SVG scales to the card, so a long session shrinks its own
    # labels into illegibility - 18 nodes rendered at 37% is a picture of
    # nothing. Keep a readable number, centred on the first interesting node
    # (untrusted context, egress, or a block) rather than the start, because
    # that is what a reader came to look at.
    MAX_NODES = 13
    truncated = 0
    if len(nodes) > MAX_NODES:
        focus_i = next((i for i, n in enumerate(nodes) if n.get("danger")), 0)
        lo = max(0, min(focus_i - MAX_NODES // 2, len(nodes) - MAX_NODES))
        truncated = len(nodes) - MAX_NODES
        nodes = nodes[lo:lo + MAX_NODES]
        keep = {n["id"] for n in nodes}
        edges = [e for e in edges if e[0] in keep and e[1] in keep]
        base = min(n["x"] for n in nodes)
        for n in nodes:
            n["x"] = n["x"] - base + 1

    pos = {n["id"]: n for n in nodes}
    W, H = int(max([n["x"] for n in nodes] or [1]) * 165 + 200), 430
    NW, NH = 132, 46

    def cx(n):  # node centre
        return 150 + (n["x"] - 1) * 165 + NW / 2

    # width:100% + viewBox so the whole path scales into the card. Natural size
    # pushed the egress - the payoff of the entire query - off the right edge
    # behind a horizontal scrollbar nobody would think to drag.
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" height="auto" '
           f'style="max-width:{W}px;display:block" preserveAspectRatio="xMinYMin meet" '
           f'xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="session trajectory graph">']
    for key, label in LANES:
        y = LANE_Y[key]
        out.append(f'<line x1="140" y1="{y + NH/2}" x2="{W-20}" y2="{y + NH/2}" '
                   f'stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text class="lane" x="12" y="{y + NH/2 + 4}">{e(label)}</text>')
    for a, b, kind, danger in edges:
        if a not in pos or b not in pos:
            continue
        na, nb = pos[a], pos[b]
        x1, y1 = cx(na), LANE_Y[na["lane"]] + NH / 2
        x2, y2 = cx(nb), LANE_Y[nb["lane"]] + NH / 2
        col = "var(--crit)" if danger else "var(--axis)"
        wdt = 2.2 if danger else 1.2
        dash = ' stroke-dasharray="5 3"' if kind == "FLOWED_INTO" else ""
        mid = (y1 + y2) / 2
        out.append(f'<path d="M{x1},{y1} C{x1},{mid} {x2},{mid} {x2},{y2}" '
                   f'fill="none" stroke="{col}" stroke-width="{wdt}"{dash}/>')
        # No per-edge labels at all. An edge's midpoint between two lanes lands
        # exactly on the node row between them, so any label - even with a halo
        # - is clipped by a node at some layout. The three line STYLES are
        # distinguishable on their own and the legend names them once.
    for n in nodes:
        x0, y0 = cx(n) - NW / 2, LANE_Y[n["lane"]]
        stroke = "var(--crit)" if n.get("danger") else "var(--s1)"
        out.append(f'<g data-tip="{e(n["tip"])}">'
                   f'<rect x="{x0}" y="{y0}" width="{NW}" height="{NH}" rx="7" '
                   f'fill="var(--surface)" stroke="{stroke}" stroke-width="1.5"/>'
                   f'<text class="nlabel" x="{x0+10}" y="{y0+19}">{e(n["label"])}</text>'
                   f'<text class="nsub" x="{x0+10}" y="{y0+34}">{e(n["sub"])}</text>'
                   f'</g>')
    out.append("</svg>")
    out.append(
        '<div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:12px;'
        'font-size:11.5px;color:var(--ink-2)">'
        '<span><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" '
        'stroke="var(--axis)" stroke-width="1.5"/></svg> captured directly '
        '(PLANNED · DECIDED · READ)</span>'
        '<span><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" '
        'stroke="var(--crit)" stroke-width="2.2" stroke-dasharray="5 3"/></svg> '
        '<b>FLOWED_INTO — derived</b>, not in any log</span>'
        '<span><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" '
        'stroke="var(--crit)" stroke-width="2.2"/></svg> untrusted source or '
        'data leaving</span></div>')

    if truncated:
        out.append(f'<p class="sub" style="margin:8px 0 0">Showing '
                   f'{len(nodes)} of {len(nodes) + truncated} events, centred on '
                   f'the first flagged step. The full trajectory is in the '
                   f'table below.</p>')

    taint = any(k == "FLOWED_INTO" and d for _, _, k, d in edges) and \
        any(k == "WROTE_TO" for _, _, k, _ in edges)
    return "".join(out), {"nodes": len(nodes) + truncated, "edges": len(edges),
                          "taint": taint,
                          "rules": sorted({f["rule_id"] for f in finds})}


def table(rows: list[dict], cols: list[tuple[str, str]], numeric: set[str]) -> str:
    if not rows:
        return '<p class="sub">no rows</p>'
    head = "".join(f'<th class="{"n" if k in numeric else ""}">{e(t)}</th>'
                   for k, t in cols)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{"n" if k in numeric else ""}">{e(r.get(k))}</td>'
            for k, _ in cols) + "</tr>"
        for r in rows)
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def main() -> int:
    if not PROJECT:
        print("export GOOGLE_CLOUD_PROJECT", file=sys.stderr)
        return 2
    c = bigquery.Client(project=PROJECT)
    t = f"`{PROJECT}.{DATASET}`"

    quality = q(c, f"SELECT * FROM {t}.v_detection_quality ORDER BY layer, precision DESC")
    labels = q(c, f"SELECT label_source, COUNT(*) n FROM {t}.v_session_labels "
                  f"GROUP BY 1 ORDER BY 2 DESC")
    # v_findings, never `findings`: the raw table is append-only and re-written
    # by a scheduled query every 15 minutes, so COUNT(*) over it reports the
    # number of TIMES a finding was written, not the number of findings.
    by_rule = q(c, f"SELECT rule_id, COUNT(*) n FROM {t}.v_findings "
                   f"GROUP BY 1 ORDER BY 2 DESC")
    # first_ask as a JOIN, not a correlated subquery: BigQuery rejects a
    # subquery that references another table unless it can de-correlate it.
    triage = q(c, f"""
        WITH first_ask AS (
          SELECT session_id,
                 ARRAY_AGG(user_message ORDER BY turn_index LIMIT 1)[SAFE_OFFSET(0)] AS asked
          FROM {t}.trajectory_invocations
          WHERE user_message IS NOT NULL GROUP BY session_id
        )
        SELECT f.rule_id, f.layer, f.severity, ROUND(f.weighted_score,2) weighted_score,
               f.session_id, f.user_id, f.turn_count, f.detail, a.asked
        FROM {t}.v_findings_triage f
        LEFT JOIN first_ask a USING (session_id)
        ORDER BY f.weighted_score DESC, f.session_id LIMIT 20""")
    # No latency column here any more: it comes from the NATIVE plane now
    # (v_tool_health, p50/p95 split by tool_origin), and an average was the
    # wrong statistic for it anyway. What survives here is what only OUR plane
    # knows - `blocked`, the enforcement outcome.
    fleet = q(c, f"SELECT tool_name, agent_version, calls, errors, blocked, sessions, "
                 f"ROUND(error_rate,3) error_rate "
                 f"FROM {t}.v_fleet_overview ORDER BY calls DESC")
    totals = q(c, f"SELECT COUNT(*) sessions, COUNTIF(is_attack) attacks "
                  f"FROM {t}.v_session_labels")[0]
    n_find = q(c, f"SELECT COUNT(*) n FROM {t}.v_findings")[0]["n"]

    # The graph is investigated on ONE session. Prefer a session that actually
    # has a taint path - untrusted data reaching an egress tool - because that
    # is the shape the whole graph model exists to find. Fall back to the top
    # of the worklist.
    pick = q(c, f"""
        SELECT f.session_id, COUNT(DISTINCT f.rule_id) rules,
               MAX(l.context_has_untrusted) untrusted, MAX(tc.is_egress) egress
        FROM {t}.v_findings f
        JOIN {t}.trajectory_llm_calls l USING (session_id)
        JOIN {t}.trajectory_tool_calls tc USING (session_id)
        GROUP BY f.session_id
        ORDER BY (MAX(l.context_has_untrusted) AND MAX(tc.is_egress)) DESC,
                 rules DESC, f.session_id
        LIMIT 1""")
    focus = pick[0]["session_id"] if pick else (triage[0]["session_id"] if triage else None)

    g_inv = q(c, f"SELECT invocation_id, turn_index, user_message, started_at "
                 f"FROM {t}.trajectory_invocations WHERE session_id='{focus}' "
                 f"ORDER BY turn_index") if focus else []
    g_llm = q(c, f"SELECT llm_call_id, invocation_id, ts, reasoning_summary, "
                 f"response_text, context_tool_call_ids, context_has_untrusted "
                 f"FROM {t}.trajectory_llm_calls WHERE session_id='{focus}' "
                 f"ORDER BY ts") if focus else []
    g_tool = q(c, f"SELECT tool_call_id, llm_call_id, tool_name, ts, status, "
                  f"sensitivity, is_egress, result_trust_label, enforcement_rule, "
                  f"data_assets_read, external_endpoints_written "
                  f"FROM {t}.trajectory_tool_calls WHERE session_id='{focus}' "
                  f"ORDER BY ts") if focus else []
    g_find = q(c, f"SELECT rule_id, layer FROM {t}.v_findings "
                  f"WHERE session_id='{focus}'") if focus else []
    g_turns = q(c, f"SELECT turn_index, tool_name, sensitivity, is_egress, status, "
                   f"enforcement_rule, context_has_untrusted, user_message "
                   f"FROM {t}.v_session_investigator WHERE session_id='{focus}' "
                   f"ORDER BY turn_index, ts") if focus else []
    graph_svg, gstat = (session_graph(g_inv, g_llm, g_tool, g_find)
                        if g_inv else ("<p class='sub'>no session</p>", {}))

    # Coverage: which layer catches which attack. The ladder argument, measured.
    cover = q(c, f"""
        SELECT l.session_id, ANY_VALUE(l.label_source) src,
               ANY_VALUE(s.user_id) user_id,
               ARRAY_AGG(DISTINCT f.layer IGNORE NULLS ORDER BY f.layer) layers
        FROM {t}.v_session_labels l
        JOIN {t}.trajectory_sessions s USING (session_id)
        LEFT JOIN {t}.v_findings f USING (session_id)
        WHERE l.is_attack GROUP BY l.session_id
        -- Named red-team attacks first. Ordering by user_id alone sorts
        -- 'cust-...' above 'redteam-...', so on the real corpus the six
        -- illustrative attacks fell off the end of the LIMIT and the table
        -- became 40 rows of the same policy breach.
        ORDER BY (ANY_VALUE(l.label_source) = 'harness') DESC,
                 ANY_VALUE(s.user_id), l.session_id
        LIMIT 14""")
    cover_total = q(c, f"SELECT COUNTIF(is_attack) n FROM {t}.v_session_labels")[0]["n"]
    # Counted over every breaching session, not just the rows displayed - a
    # headline that silently described a 14-row sample would be worse than no
    # headline.
    allcov = q(c, f"""
        SELECT ARRAY_AGG(DISTINCT f.layer IGNORE NULLS) layers
        FROM {t}.v_session_labels l
        LEFT JOIN {t}.v_findings f USING (session_id)
        WHERE l.is_attack GROUP BY l.session_id""")
    only3 = sum(1 for r in allcov if r["layers"] == [3])
    missed = sum(1 for r in allcov if not r["layers"])
    caught1 = sum(1 for r in allcov if 1 in (r["layers"] or []))

    lbl = ", ".join(f'{r["n"]:,} {r["label_source"]}' for r in labels)
    n_attacks = totals["attacks"]
    breaches = sum(1 for r in labels if r["label_source"] == "policy_oracle"
                   for _ in range(r["n"]))
    l1 = [r for r in quality if r["layer"] == 1]
    l3 = [r for r in quality if r["layer"] == 3]
    best1 = max((r["precision"] or 0) for r in l1) if l1 else 0
    rec3 = max((r["recall"] or 0) for r in l3) if l3 else 0

    # The headline is whichever fact this corpus actually carries: an agent
    # breaching its own policy unprompted beats any detection statistic.
    if breaches:
        hero = (f'<div class="hero"><div class="n">{breaches}'
                f'<small>sessions where the agent broke its own policy, '
                f'unprompted</small></div>'
                f'<p>Nobody attacked these sessions. An ordinary customer asked '
                f'for a refund above the manifest limit and the agent paid it '
                f'out — the same request it correctly escalated in others. This '
                f'is what trajectory monitoring finds in production before it '
                f'ever finds an attacker, and it is invisible to a request log: '
                f'every call was authorised.</p></div>')
    else:
        hero = (f'<div class="hero"><div class="n">{len(quality)}'
                f'<small>rules firing across three layers on '
                f'{totals["sessions"]:,} sessions</small></div>'
                f'<p>{n_attacks} labelled attacks. Layer 1 reaches '
                f'{best1*100:.0f}% precision because it only asserts what the '
                f'manifest already states; the judge reaches {rec3*100:.0f}% '
                f'recall because it reads the transcript rather than matching a '
                f'pattern. Neither replaces the other.</p></div>')

    cov_rows = "".join(
        f'<tr><td>{e(who(r["user_id"]))}'
        f'<div class="idsub">{e(r["session_id"][:8])} · {e(r["src"])}</div></td>'
        + "".join(f'<td class="{"hit" if n in (r["layers"] or []) else "miss"}">'
                  f'{"caught" if n in (r["layers"] or []) else "not seen"}</td>'
                  for n in (1, 2, 3))
        + "</tr>" for r in cover)

    tri_rows = "".join(
        f'<tr><td><b>{e(means(r["rule_id"]))}</b>'
        f'<div class="idsub">{e(r["rule_id"])} · layer {r["layer"]}</div></td>'
        f'<td>{e(who(r["user_id"]))}'
        f'<div class="idsub">session {e(r["session_id"][:8])} · '
        f'{r["turn_count"] or 0} turns</div></td>'
        f'<td>{e((r.get("asked") or "")[:90])}</td>'
        f'<td class="n">{r["weighted_score"]}</td></tr>' for r in triage)

    # The story: what was asked, what the agent did, what fired.
    asked = next((i.get("user_message") for i in g_inv if i.get("user_message")), "")
    seq = " → ".join(dict.fromkeys(tc["tool_name"] for tc in g_tool)) or "no tools"
    fired = sorted({f["rule_id"] for f in g_find})
    fired_html = "".join(
        f'<li><b>{e(means(rid))}</b> <span class="idsub">{e(rid)}</span></li>'
        for rid in fired) or "<li>nothing fired</li>"
    story_who = who(next((s0.get("user_id") for s0 in
                          q(c, f"SELECT user_id FROM {t}.trajectory_sessions "
                              f"WHERE session_id='{focus}'")), ""))

    body = f"""
<div class="wrap">
<h1>Agent trajectory detection</h1>
<p class="sub">What a support agent actually did, session by session — and which
of it broke policy. <code>{e(PROJECT)}.{e(DATASET)}</code> · ground truth:
{e(lbl)}</p>

{hero}

<div class="kpis">
  <div class="kpi"><div class="v">{totals['sessions']:,}</div><div class="l">sessions watched</div></div>
  <div class="kpi"><div class="v">{n_attacks:,}</div><div class="l">sessions that broke policy</div></div>
  <div class="kpi"><div class="v">{n_find:,}</div><div class="l">findings raised</div></div>
  <div class="kpi"><div class="v">{len(quality)}</div><div class="l">detectors firing</div></div>
</div>

<h2>1 · One session, start to finish</h2>
<p class="sub">Every claim on this page comes from trajectories like this one.
Read it before the charts.</p>

<div class="story">
  <div class="step"><span class="k">Who</span>
    <div>{e(story_who)}<div class="idsub">session {e(focus or '')}</div></div></div>
  <div class="step"><span class="k">What they asked</span>
    <div class="quote">“{e(asked[:200] or 'no message captured')}”</div></div>
  <div class="step"><span class="k">What the agent did</span>
    <div><code>{e(seq)}</code></div></div>
  <div class="step"><span class="k">What fired</span>
    <ul class="fired">{fired_html}</ul></div>
</div>

<div class="gwrap">{graph_svg}</div>
<div class="insight {'warn' if gstat.get('taint') else ''}">
  <b>{'Untrusted data reached a tool that sends data out of the company.'
      if gstat.get('taint') else
      'Four separate logs. One picture. The breach is the path between them.'}</b>
  <p>Each lane is a different stream the agent emits, captured independently and
  joined by id. The dashed red edge, <b>FLOWED_INTO</b>, appears in none of them
  — it is derived by asking which tool results were sitting in the model's
  context when it made each decision. That single derived edge is what turns
  four logs into the question you actually want to ask:
  <i>“did untrusted content influence a call that sent data outside?”</i>
  It is why Lab 2.1 captures <code>context_tool_call_ids</code>, and why losing
  that one field on real traffic silently reduced this graph to disconnected
  points.</p>
</div>

<div class="card"><p class="sub" style="margin:0 0 8px">The same session as a
table — every step, in order, including the ones the graph trimmed.</p>
{table(g_turns, [
    ("turn_index","turn"),("user_message","what the customer said"),
    ("tool_name","tool"),("sensitivity","sensitivity"),("is_egress","sends data out"),
    ("status","status"),("context_has_untrusted","model saw untrusted"),
    ("enforcement_rule","blocked by")], {"turn_index"})}</div>

<h2>2 · Does it generalise?</h2>
<p class="sub">Every session we know broke policy, and which layer noticed.
“Not seen” is not a bug — it is the argument for having three layers.</p>
<div class="card"><div class="scroll"><table class="matrix">
<tr><th>what it was</th><th class="n">Layer 1 · rules</th>
<th class="n">Layer 2 · anomaly</th><th class="n">Layer 3 · judge</th></tr>
{cov_rows}</table></div>
<p class="sub" style="margin:10px 0 0">Showing {len(cover)} of {cover_total}
policy-breaking sessions, named red-team attacks first.</p></div>
<div class="insight">
  <b>{only3} of {len(allcov)} were caught only by the judge. {missed} by nothing at all.</b>
  <p>Layer 1 caught {caught1}: everything you could write a rule for in advance,
  at perfect precision — which is why it is the only layer allowed to block a
  tool call inline. What a rule cannot catch is the attack nobody enumerated. A
  multi-turn crescendo that never calls an egress tool leaves nothing to assert
  on; only something that reads the conversation sees it. That is the whole
  reason to pay for a third layer.</p>
  <p style="margin-top:8px"><b>Read “not seen” carefully.</b> On a live-model
  corpus some attacks are refused outright — the agent calls no tools at all —
  so there is no trajectory to detect and every layer is correctly silent. That
  is the agent defending itself, not a detector missing. Check the session
  before counting it as a miss: it is the difference between “we failed” and
  “nothing happened”.</p>
</div>

<h2>3 · How good are the detectors?</h2>
<p class="sub">Precision = when it fires, is it right. Recall = of everything
that broke policy, how much did it find. Each is scored against the sessions
that detector actually saw.</p>
<div class="card">{quality_chart(quality)}</div>
<div class="insight">
  <b>Nothing here is both precise and complete — that is the trade you are buying.</b>
  <p>Check <code>basis</code> before comparing two rows: Layers 1–2 see every
  session, the judge sees a sample, and scoring the judge against sessions it
  was never shown drops its recall from 0.989 to 0.957 for no reason. Where a
  rule and the ground-truth oracle test the same thing, precision is true by
  construction rather than measured — stated here rather than quietly enjoyed.</p>
</div>
<div class="card">{table(quality, [
    ("rule_id","detector"),("layer","layer"),("basis","scored against"),
    ("flagged_sessions","fired on"),("true_positives","right"),("false_positives","wrong"),
    ("sessions_evaluated","sessions seen"),("attacks_in_scope","breaches in scope"),
    ("enforcement_candidate","safe to block?")],
    {"layer","flagged_sessions","true_positives","false_positives",
     "sessions_evaluated","attacks_in_scope"})}</div>

<h2>4 · The worklist</h2>
<p class="sub">What an analyst would open tomorrow morning, highest score first.</p>
<div class="card"><div class="scroll"><table>
<tr><th>what happened</th><th>who</th><th>what they asked</th><th class="n">score</th></tr>
{tri_rows}</table></div></div>

<h2>5 · Fleet</h2>
<p class="sub">Volume and health — the operational view a managed runtime gives
you for free, and the one that shows nothing above.</p>
<div class="card">{table(fleet, [
    ("tool_name","tool"),("agent_version","version"),("calls","calls"),
    ("errors","errors"),("blocked","blocked"),("sessions","sessions"),
    ("error_rate","error rate")],
    {"calls","errors","blocked","sessions","error_rate"})}</div>

<p class="foot">Generated by <code>labs/dashboard.py</code> from five BigQuery
views. The logic lives in <code>sql/views/dashboard_views.sql</code>, not in this
page — which is why a customer can point it at their own dataset and it just
works.</p>
</div>
<div id="tip"></div>
"""
    OUT.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Agent trajectory detection — {e(DATASET)}</title>'
        f"<style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>")
    print(f"  wrote {OUT}  ({len(quality)} rules, {n_find:,} findings, "
          f"{totals['sessions']:,} sessions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
