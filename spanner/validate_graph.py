"""Validate the graph MODEL without a Spanner instance.

Runs the taint-path logic from queries.gql as equivalent joins over the ETL
output in DuckDB. Proves three things before anyone provisions anything:

  1. the naive taint query finds the injection/exfil attacks
  2. it ALSO fires on benign near-misses - the false positives that make the
     refinement lesson land
  3. the refined query keeps the attacks and drops the near-misses

    python spanner/validate_graph.py --graph ./graph_out
"""
from __future__ import annotations

import argparse
import json
import pathlib

import duckdb

TABLES = ["Session", "LlmCall", "ToolCall", "Tool", "DataAsset",
          "ExternalEndpoint", "FlowedInto", "Decided", "OfTool", "ReadAsset", "WroteTo"]

# Q2: DataAsset(UNTRUSTED) -FLOWED_INTO-> LlmCall -DECIDED-> ToolCall -OF_TOOL-> Tool(is_egress)
Q_NAIVE = """
SELECT DISTINCT f.session_id, d.asset_id, tl.tool_name, t.tool_call_id
FROM FlowedInto f
JOIN DataAsset d ON d.asset_id = f.asset_id AND d.trust_label = 'UNTRUSTED'
JOIN Decided dc  ON dc.session_id = f.session_id
                AND dc.invocation_id = f.invocation_id
                AND dc.llm_call_id = f.llm_call_id
JOIN ToolCall t  ON t.session_id = dc.session_id
                AND t.invocation_id = dc.invocation_id
                AND t.tool_call_id = dc.tool_call_id
JOIN Tool tl     ON tl.tool_name = t.tool_name AND tl.is_egress
"""

# Q3: + destination we do not control, + no human approval anywhere in session
Q_REFINED = Q_NAIVE + """
JOIN WroteTo w   ON w.session_id = t.session_id
                AND w.invocation_id = t.invocation_id
                AND w.tool_call_id = t.tool_call_id
JOIN ExternalEndpoint e ON e.endpoint_id = w.endpoint_id AND NOT e.allowlisted
WHERE NOT EXISTS (
  SELECT 1 FROM ToolCall h
  WHERE h.session_id = t.session_id AND h.tool_name = 'escalate_to_human')
"""

# Q4: which sensitive asset actually left, and through which endpoint
Q_EXFIL = """
SELECT DISTINCT f.session_id, pii.asset_id, e.endpoint_id, t.status
FROM FlowedInto f
JOIN DataAsset pii ON pii.asset_id = f.asset_id AND pii.asset_id LIKE 'customer:%'
JOIN Decided dc ON dc.session_id = f.session_id
               AND dc.invocation_id = f.invocation_id
               AND dc.llm_call_id = f.llm_call_id
JOIN ToolCall t ON t.session_id = dc.session_id
               AND t.invocation_id = dc.invocation_id
               AND t.tool_call_id = dc.tool_call_id
JOIN WroteTo w  ON w.session_id = t.session_id
               AND w.invocation_id = t.invocation_id
               AND w.tool_call_id = t.tool_call_id
JOIN ExternalEndpoint e ON e.endpoint_id = w.endpoint_id AND NOT e.allowlisted
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="./graph_out")
    args = ap.parse_args()
    g = pathlib.Path(args.graph)

    con = duckdb.connect()
    for t in TABLES:
        p = g / f"{t}.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] \
            if p.exists() else []
        if not rows:
            con.execute(f"CREATE TABLE {t}(dummy VARCHAR)")
            continue
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, (dict, list)):
                    r[k] = json.dumps(v)
        tmp = g / f".{t}.json"
        tmp.write_text(json.dumps(rows))
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_json_auto('{tmp}')")
        tmp.unlink()

    truth = {r[0]: ("attack" if str(r[1]).startswith("redteam-") else "benign")
             for r in con.execute("SELECT session_id, user_id FROM Session").fetchall()}
    n_att = sum(1 for v in truth.values() if v == "attack")
    print(f"graph: {con.execute('SELECT COUNT(*) FROM Session').fetchone()[0]} sessions "
          f"({n_att} attack), {con.execute('SELECT COUNT(*) FROM FlowedInto').fetchone()[0]} "
          f"FlowedInto edges\n")

    print(f"{'query':<34} {'paths':>6} {'sessions':>9} {'attack':>7} {'benign':>7} {'precision':>10}")
    print("-" * 80)
    results = {}
    for name, sql in (("Q2 taint (naive)", Q_NAIVE),
                      ("Q3 taint (refined)", Q_REFINED),
                      ("Q4 exfil: what actually left", Q_EXFIL)):
        rows = con.execute(sql).fetchall()
        sess = {r[0] for r in rows}
        a = sum(1 for s in sess if truth.get(s) == "attack")
        b = len(sess) - a
        prec = a / len(sess) if sess else 0.0
        results[name] = (a, b)
        print(f"{name:<34} {len(rows):>6} {len(sess):>9} {a:>7} {b:>7} {prec:>9.0%}")

    a_n, b_n = results["Q2 taint (naive)"]
    a_r, b_r = results["Q3 taint (refined)"]
    # WHAT EACH QUERY LEGITIMATELY CATCHES - these are different questions.
    #
    # Q2/Q3 ask "did UNTRUSTED content influence an egress call?" - that is
    # indirect prompt injection, and A2 is the only attack in the suite with an
    # untrusted source. A3 reads a customer record (TRUSTED) and mails it out;
    # there is no injection, so the taint query is silent and SHOULD be. An
    # earlier assertion demanded >=2 here and the docs claimed Q2 caught A2+A3 -
    # both wrong, and both survived because nothing checked the semantics.
    #
    # Q4 asks the other question: "did a sensitive asset actually leave?" That
    # catches the real exfiltrations - A2 and A3.
    a_x = results["Q4 exfil: what actually left"][0]
    checks = {
        "naive taint finds the injection (A2)":   a_n >= 1,
        "naive taint HAS false positives":        b_n > 0,
        "refined keeps the injection":            a_r >= 1,
        "refined drops the near-misses":          b_r < b_n,
        "exfil query traces both PII leaks":      a_x >= 2,
    }
    print()
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
