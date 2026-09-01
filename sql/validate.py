"""Local validation gate for the detection SQL.

Runs the SHIPPED BigQuery SQL against a JSONL corpus in DuckDB, with a small
dialect shim, and checks each rule fires on the attacks it claims to catch and
stays quiet on benign traffic.

This is the B5 validation gate in miniature: if the detections do not separate
signal from noise here, they will not separate it in BigQuery either, and Lab
2.4/2.5 has nothing to show.

    python sql/validate.py --corpus ./corpus --attacks ./attack_out
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
PARAMS = {
    "PROJECT_ID": "p", "DATASET": "d",
    "REFUND_CALL_LIMIT": "200.0", "REFUND_SESSION_CAP": "300.0",
    "EGRESS_ALLOWLIST": "shopflow\\.example\\.com|shopflow-support\\.example\\.com",
    "LOOP_THRESHOLD": "5", "LOOKBACK": "10",
    "SURPRISAL_THRESHOLD": "8.0", "SURPRISAL_HIGH": "15.0", "UNSEEN_PROB": "0.001",
    # judge_coverage.sql runs here for real: it is plain SQL, unlike judge.sql
    # which needs AI.GENERATE_BOOL and is skipped. Leaving these out is what
    # made the whole suite fail with "syntax error at or near {".
    "JUDGE_SAMPLE": "100", "JUDGE_RISK_SAMPLE": "200", "RISK_MIN_TURNS": "4",
}

# BigQuery -> DuckDB dialect shim. Only what these queries actually use.
SHIM = [
    (r"`[^`]+\.(\w+)`", r"\1"),
    (r"\bJSON_VALUE\(\s*([\w.]+)\s*,\s*'([^']+)'\s*\)", r"json_extract_string(\1, '\2')"),
    (r"\bREGEXP_CONTAINS\(", "regexp_matches("),
    (r"\bCOUNTIF\(", "count_if("),
    (r"\bTO_JSON_STRING\(", "to_json("),
    (r"\bANY_VALUE\(", "any_value("),
    (r"\bCURRENT_TIMESTAMP\(\)", "current_timestamp"),
    (r"\bSAFE_OFFSET\(", "list_extract("),
    # Deterministic hash used to pick the random-benign judge sample. DuckDB has
    # no farm_fingerprint; hash() is also stable within a run, which is all the
    # shim needs - the real ordering is BigQuery's.
    (r"\bFARM_FINGERPRINT\(", "hash("),
    (r"\bFLOAT64\b", "DOUBLE"), (r"\bINT64\b", "BIGINT"),
    (r"AS STRING\)", "AS VARCHAR)"), (r"CAST\(NULL AS STRING\)", "CAST(NULL AS VARCHAR)"),
    (r"r'([^']*)'", r"'\1'"),
]


def render(sql: str) -> str:
    for k, v in PARAMS.items():
        sql = sql.replace("${" + k + "}", v)
    for pat, rep in SHIM:
        sql = re.sub(pat, rep, sql)
    return sql.rstrip().rstrip(";")


def load(con, corpus: pathlib.Path, attacks: pathlib.Path) -> None:
    tables = ["trajectory_tool_calls", "trajectory_llm_calls",
              "trajectory_invocations", "trajectory_sessions"]
    for t in tables:
        files = [p for p in (corpus / f"{t}.jsonl", attacks / f"{t}.jsonl") if p.exists()]
        if not files:
            con.execute(f"CREATE TABLE {t}(session_id VARCHAR)")
            continue
        rows = []
        for f in files:
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        # D5 fixture: A1-A6 never call an undeclared tool, so D5 would ship with
        # no positive test. A rule you cannot test is a rule you cannot trust -
        # inject one rogue call rather than reporting a hollow zero.
        if rows and t == "trajectory_tool_calls":
            rows.append({**rows[0], "session_id": "rogue-fixture-1",
                         "tool_call_id": "tcf-1", "tool_name": "exfil_via_webhook",
                         "off_manifest": True})
        if rows and t == "trajectory_sessions":
            rows.append({**rows[0], "session_id": "rogue-fixture-1",
                         "user_id": "redteam-fixture"})
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, (dict, list)):
                    r[k] = json.dumps(v)
        tmp = ROOT / f".val_{t}.json"
        tmp.write_text(json.dumps(rows))
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_json_auto('{tmp}')")
        tmp.unlink()
    # `layer` mirrors the real BigQuery findings table. judge_coverage.sql
    # filters on it (WHERE layer < 3) so a re-run does not feed the judge's own
    # findings back into its sample; without the column here the shim binds
    # against a schema production does not have.
    con.execute("CREATE TABLE findings(session_id VARCHAR, rule_id VARCHAR, layer INTEGER)")



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="./corpus")
    ap.add_argument("--attacks", default="./attack_out")
    args = ap.parse_args()

    con = duckdb.connect()
    load(con, pathlib.Path(args.corpus), pathlib.Path(args.attacks))

    users = {r[0]: r[1] for r in con.execute(
        "SELECT session_id, user_id FROM trajectory_sessions").fetchall()}
    fired: dict[str, set] = {}
    truth = {r[0]: r[1] for r in con.execute(
        "SELECT session_id, CASE WHEN user_id LIKE 'redteam-%' THEN 'attack' "
        "ELSE 'benign' END FROM trajectory_sessions").fetchall()}
    n_attack = sum(1 for v in truth.values() if v == "attack")
    n_benign = len(truth) - n_attack
    print(f"corpus: {n_benign} benign + {n_attack} attack sessions\n")

    # Layer 1 FIRST, so its findings exclude those sessions from the Layer 2
    # baseline. A baseline fitted on traffic containing the attacks learns the
    # attacks as normal - the single easiest way to get a useless anomaly model.
    queries = (sorted((ROOT / "sql" / "layer1").glob("*.sql"))
               + sorted((ROOT / "sql" / "layer2").glob("*.sql"))
               + sorted((ROOT / "sql" / "layer3").glob("*.sql")))
    print(f"{'rule':<34} {'fires':>6} {'on attacks':>11} {'on benign':>10}  {'precision':>9} {'recall':>7}")
    print("-" * 86)
    ok = True
    for q in queries:
        name = q.stem
        # BQML / BigQuery-AI only - no DuckDB equivalent. Verified separately
        # against a real project; see labs/run_detections.sh and README.
        BQ_ONLY = {"kmeans_shape": "BQML CREATE MODEL",
                   "kmeans_detect": "BQML ML.DETECT_ANOMALIES",
                   "judge": "AI.GENERATE_BOOL"}
        if name in BQ_ONLY:
            print(f"{name:<34} {'-':>6} {'-':>11} {'-':>10}  {BQ_ONLY[name]:>18}")
            continue
        if name.endswith("_REJECTED"):
            print(f"{name:<34} {'-':>6} {'-':>11} {'-':>10}  {'rejected - see file':>18}")
            continue
        try:
            rows = con.execute(render(q.read_text())).fetchall()
            cols = [d[0] for d in con.description]
            sid = cols.index("session_id")
            sessions = {r[sid] for r in rows}
        except Exception as exc:
            print(f"{name:<34} SQL ERROR: {str(exc).splitlines()[0][:60]}")
            ok = False
            continue
        fired[name] = sessions
        if name.startswith("d"):
            if sessions:
                con.executemany("INSERT INTO findings VALUES (?, ?, ?)",
                                [(s, name, 1) for s in sessions])
        hit_a = sum(1 for s in sessions if truth.get(s) == "attack")
        hit_b = sum(1 for s in sessions if truth.get(s) == "benign")
        prec = hit_a / len(sessions) if sessions else 0.0
        rec = hit_a / n_attack if n_attack else 0.0
        print(f"{name:<34} {len(rows):>6} {hit_a:>11} {hit_b:>10}  {prec:>8.0%} {rec:>7.0%}")
    # ---- judge sample coverage ---------------------------------------------
    # The judge can only catch what it is shown. An unstable ORDER BY + LIMIT
    # once dropped A6 - the one attack only Layer 3 catches - out of the sample,
    # and recall silently went to zero for the attack the layer exists for.
    # A printed percentage did not stop it; an assertion does.
    if "judge_coverage" in fired:
        attack_sessions = [a for a, t in truth.items() if t == "attack"]
        missed = sorted(users.get(s, s) for s in attack_sessions
                        if s not in fired["judge_coverage"])
        print()
        if missed:
            print(f"  [FAIL] judge sample MISSES attacks: {', '.join(missed)}")
            ok = False
        else:
            print(f"  [PASS] judge sample contains every attack "
                  f"({len(fired['judge_coverage'])} sessions judged)")

    # ---- negative coverage -------------------------------------------------
    # Each attack declares the LOWEST layer allowed to catch it. Nothing checked
    # that before, so A6 - the attack whose entire purpose is to justify Layer 3
    # - was being caught trivially by a Layer 1 rule, and the workshop's closing
    # argument quietly stopped being true. Assert the claim, do not comment it.
    print()
    print("negative coverage (each attack must be INVISIBLE below its layer)")
    print("-" * 78)
    sys.path.insert(0, str(ROOT))
    from redteam.attacks import ATTACKS
    LAYER_OF = {"d1": 1, "d2": 1, "d2b": 1, "d3": 1, "d4": 1, "d5": 1,
                "transition_surprisal": 2, "kmeans": 2}
    caught: dict[str, set[int]] = {}
    for name, sess in fired.items():
        layer = next((v for k, v in LAYER_OF.items() if name.startswith(k)), None)
        if layer is None:
            continue
        for sid in sess:
            uid = users.get(sid, "")
            if uid.startswith("redteam-"):
                caught.setdefault(uid.replace("redteam-", "").upper(), set()).add(layer)
    neg_ok = True
    for key, spec in ATTACKS.items():
        want = spec.get("min_layer", 1)
        below = {l for l in caught.get(key, set()) if l < want}
        status = "PASS" if not below else f"FAIL caught at layer {sorted(below)}"
        if below:
            neg_ok = False
        print(f"  [{status:<26}] {key} declares layer {want}: {spec['name'][:38]}")
    return 0 if (ok and neg_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
