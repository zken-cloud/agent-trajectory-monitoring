"""BigQuery -> Spanner Graph ETL.

Builds the node and edge tables for TrajectoryGraph, including deriving the
load-bearing FLOWED_INTO edge.

Two modes so the graph model is testable without provisioning anything:
    python spanner/etl.py --source ./corpus,./attack_out --out ./graph_out
    python spanner/etl.py --source bigquery --instance shopflow --database traj

Only the T2 subset goes to Spanner (~300 stratified sessions). The full T1
corpus stays in BigQuery. Graph labs fail from too MUCH data, not too little:
300 sessions is ~7.5k nodes and explorable in a notebook; 2,000 is not.
"""
from __future__ import annotations

import argparse

# The Spanner client exports internal metrics to Cloud Monitoring and prints a
# 400-with-a-wall-of-JSON when it cannot ("resource labels ... missing
# instance_id"). Nothing failed, but it lands after a successful load and reads
# exactly like one, so attendees chase it. Off by default here.
import os
os.environ.setdefault("SPANNER_DISABLE_BUILTIN_METRICS", "true")
import json
import pathlib
import random
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "agent_trajectory"))
from agent_trajectory import manifest as _manifest   # noqa: E402

TABLES = ["trajectory_sessions", "trajectory_invocations",
          "trajectory_llm_calls", "trajectory_tool_calls"]


def read_jsonl(dirs: list[pathlib.Path]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in TABLES}
    for d in dirs:
        for t in TABLES:
            p = d / f"{t}.jsonl"
            if p.exists():
                out[t] += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return out


def read_bigquery(project: str, dataset: str) -> dict[str, list[dict]]:
    from google.cloud import bigquery
    client = bigquery.Client(project=project)
    return {t: [dict(r) for r in client.query(
                f"SELECT * FROM `{project}.{dataset}.{t}`").result()]
            for t in TABLES}


def stratified_subset(data: dict, n: int, seed: int = 20260810) -> set[str]:
    """T2 selection - STRATIFIED, never a random sample.

    All attack sessions, every flagged session, and a benign remainder that
    deliberately INCLUDES near-misses (untrusted read -> PII read -> allowlisted
    egress). Without those the taint query produces no false positives and
    attendees never learn the refinement, which is the whole lesson of Lab 2.3.
    """
    rng = random.Random(seed)
    by_session = defaultdict(list)
    for tc in data["trajectory_tool_calls"]:
        by_session[tc["session_id"]].append(tc)

    attacks, near_miss, plain = [], [], []
    for s in data["trajectory_sessions"]:
        sid = s["session_id"]
        calls = by_session.get(sid, [])
        if str(s.get("user_id", "")).startswith("redteam-") or s.get("outcome") == "attack":
            attacks.append(sid)
        elif any(c.get("is_egress") for c in calls) and \
             any(c.get("sensitivity") in ("pii", "financial") for c in calls):
            near_miss.append(sid)          # the deliberate false positives
        else:
            plain.append(sid)

    want_nm = min(len(near_miss), max(1, int(n * 0.25)))
    rest = max(0, n - len(attacks) - want_nm)
    chosen = set(attacks) | set(rng.sample(near_miss, want_nm)) \
             | set(rng.sample(plain, min(len(plain), rest)))
    print(f"  T2 subset: {len(attacks)} attack + {want_nm} near-miss "
          f"+ {min(len(plain), rest)} plain = {len(chosen)} sessions")
    return chosen


def build(data: dict, mf: _manifest.Manifest, keep: set[str]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    seen_assets: dict[str, str] = {}
    seen_endpoints: dict[str, bool] = {}

    def in_scope(r):
        return r.get("session_id") in keep

    # --- global dimension nodes ---
    for name, pol in mf.tools.items():
        g["Tool"].append({"tool_name": name, "sensitivity": pol.sensitivity,
                          "is_egress": pol.is_egress, "is_mutating": pol.is_mutating,
                          "trust_label": pol.trust_label, "off_manifest": False})
    g["Agent"].append({"agent_name": mf.agent_name, "agent_version": mf.agent_version,
                       "runs_as": f"sa:{mf.agent_name}@agent"})
    g["Principal"].append({"principal_id": f"sa:{mf.agent_name}@agent",
                           "kind": "service_account"})
    g["RunsAs"].append({"agent_name": mf.agent_name, "agent_version": mf.agent_version,
                        "principal_id": f"sa:{mf.agent_name}@agent"})

    # --- sessions / invocations ---
    for s in data["trajectory_sessions"]:
        if not in_scope(s):
            continue
        g["Session"].append({k: s.get(k) for k in
                             ("session_id", "user_id", "agent_name", "agent_version",
                              "started_at", "ended_at", "turn_count", "outcome")}
                            | {"flagged": False})
        pid = f"user:{s.get('user_id')}"
        if pid not in seen_endpoints:
            g["Principal"].append({"principal_id": pid, "kind": "end_user"})
            seen_endpoints[pid] = True
        g["Started"].append({"principal_id": pid, "session_id": s["session_id"]})

    for i in data["trajectory_invocations"]:
        if not in_scope(i):
            continue
        g["Invocation"].append({k: i.get(k) for k in
                                ("session_id", "invocation_id", "turn_index",
                                 "user_message", "started_at")})
        g["HasInvocation"].append({"session_id": i["session_id"],
                                   "invocation_id": i["invocation_id"]})

    # --- llm calls ---
    for l in data["trajectory_llm_calls"]:
        if not in_scope(l):
            continue
        g["LlmCall"].append({k: l.get(k) for k in
                             ("session_id", "invocation_id", "llm_call_id", "model",
                              "ts", "reasoning_summary", "finish_reason",
                              "context_has_untrusted")})
        g["Planned"].append({"session_id": l["session_id"],
                             "invocation_id": l["invocation_id"],
                             "llm_call_id": l["llm_call_id"]})

    # --- tool calls, assets, endpoints ---
    tc_by_id: dict[str, dict] = {}
    for t in data["trajectory_tool_calls"]:
        if not in_scope(t):
            continue
        tc_by_id[t["tool_call_id"]] = t
        pol = mf.tool(t["tool_name"])
        g["ToolCall"].append({k: t.get(k) for k in
                              ("session_id", "invocation_id", "tool_call_id",
                               "llm_call_id", "tool_name", "ts", "status",
                               "sensitivity", "is_egress", "args_redacted",
                               "enforcement_rule")})
        g["OfTool"].append({"session_id": t["session_id"],
                            "invocation_id": t["invocation_id"],
                            "tool_call_id": t["tool_call_id"],
                            "tool_name": t["tool_name"]})
        if t.get("llm_call_id"):
            g["Decided"].append({"session_id": t["session_id"],
                                 "invocation_id": t["invocation_id"],
                                 "llm_call_id": t["llm_call_id"],
                                 "tool_call_id": t["tool_call_id"]})
        for asset in _aslist(t.get("data_assets_read")):
            # Asset trust is inherited from the TOOL that produced it. KB docs
            # are untrusted because search_kb is declared UNTRUSTED in the
            # manifest - the taint source is a config fact, not a hardcoded rule.
            seen_assets[asset] = pol.trust_label
            g["ReadAsset"].append({"session_id": t["session_id"],
                                   "invocation_id": t["invocation_id"],
                                   "tool_call_id": t["tool_call_id"],
                                   "asset_id": asset})
        for ep in _aslist(t.get("external_endpoints_written")):
            domain = ep.rsplit("@", 1)[-1].lower()
            seen_endpoints[ep] = domain in mf.egress_allowlist_domains
            g["WroteTo"].append({"session_id": t["session_id"],
                                 "invocation_id": t["invocation_id"],
                                 "tool_call_id": t["tool_call_id"],
                                 "endpoint_id": ep})

    for asset, trust in seen_assets.items():
        g["DataAsset"].append({"asset_id": asset, "asset_kind": asset.split(":")[0],
                               "trust_label": trust})
    for ep, allow in seen_endpoints.items():
        if ep.startswith(("user:", "sa:")):
            continue
        g["ExternalEndpoint"].append({"endpoint_id": ep,
                                      "domain": ep.rsplit("@", 1)[-1].lower(),
                                      "allowlisted": allow})

    # ===== THE DERIVED TAINT EDGE ==========================================
    # For each LLM call, every tool result that was in its CONTEXT WINDOW
    # contributes its data assets as inputs that could have influenced it.
    # context_tool_call_ids is the field captured in before_model - without it
    # this loop has nothing to iterate and the graph is just a picture.
    n_flowed = 0
    for l in data["trajectory_llm_calls"]:
        if not in_scope(l):
            continue
        for ctx_id in _aslist(l.get("context_tool_call_ids")):
            src = tc_by_id.get(ctx_id)
            if src is None:
                continue
            for asset in _aslist(src.get("data_assets_read")):
                g["FlowedInto"].append({
                    "session_id": l["session_id"],
                    "invocation_id": l["invocation_id"],
                    "asset_id": asset,
                    "llm_call_id": l["llm_call_id"],
                    "via_tool_call_id": ctx_id,
                    "trust_label": seen_assets.get(asset, "TRUSTED"),
                })
                n_flowed += 1
    print(f"  derived FlowedInto edges: {n_flowed}")
    return g


def _aslist(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [v]
    return list(v) if isinstance(v, list) else []


# Spanner is strictly typed on the wire: TIMESTAMP columns need real datetimes
# and JSON columns need JsonObject. JSONL round-trips both as strings, so the
# load fails with "Expected TIMESTAMP" unless they are coerced back. This only
# shows up against a real instance - the DuckDB model validation cannot catch it.
_TS_COLS = {"ts", "started_at", "ended_at"}
_JSON_COLS = {"args_redacted"}


def _coerce(col: str, value):
    if value is None:
        return None
    if col in _TS_COLS and isinstance(value, str):
        import datetime as _dt
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if col in _JSON_COLS:
        from google.cloud.spanner_v1 import JsonObject
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return None
        return JsonObject(value) if isinstance(value, dict) else None
    return value


# Root tables. Everything else is INTERLEAVE IN PARENT ... ON DELETE CASCADE,
# so clearing these clears the whole graph.
_ROOTS = ["Session", "DataAsset", "ExternalEndpoint", "Principal", "Tool", "Agent"]


def truncate_spanner(db) -> None:
    """A load is a full rebuild of the T2 subset, so clear first.

    insert_or_update alone silently MERGES the new graph into whatever was
    there. Re-running the ETL after any fix then leaves a hybrid, and the query
    counts drift from the lab guide with nothing to show for it - which is
    exactly what happened here: a stale database reported 101 paths for Q2
    where the freshly built graph gives 92.
    """
    for table in _ROOTS:
        db.execute_partitioned_dml(f"DELETE FROM {table} WHERE TRUE")


def write_spanner(g: dict, instance: str, database: str, project: str,
                  truncate: bool = True) -> None:
    from google.cloud import spanner
    client = spanner.Client(project=project)
    db = client.instance(instance).database(database)
    if truncate:
        truncate_spanner(db)
    for table, rows in g.items():
        if not rows:
            continue
        cols = list(rows[0])
        for i in range(0, len(rows), 500):          # mutation batches
            chunk = rows[i:i + 500]
            with db.batch() as batch:
                batch.insert_or_update(
                    table=table, columns=cols,
                    values=[[_coerce(c, r.get(c)) for c in cols] for r in chunk])
        print(f"  {table:<18} {len(rows):>6} rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="./corpus,./attack_out")
    ap.add_argument("--out", default=None, help="local mode: write graph tables as JSONL")
    ap.add_argument("--sessions", type=int, default=300, help="T2 subset size")
    ap.add_argument("--project", default=None)
    ap.add_argument("--dataset", default="trajectory")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--database", default=None)
    ap.add_argument("--append", action="store_true",
                    help="merge into the existing graph instead of rebuilding it")
    args = ap.parse_args()

    mf = _manifest.load(ROOT / "config" / "tool_manifest.yaml")
    if args.source == "bigquery":
        data = read_bigquery(args.project, args.dataset)
    else:
        data = read_jsonl([pathlib.Path(p) for p in args.source.split(",")])
    print(f"  loaded {len(data['trajectory_sessions'])} sessions, "
          f"{len(data['trajectory_tool_calls'])} tool calls")

    keep = stratified_subset(data, args.sessions)
    g = build(data, mf, keep)

    if args.out:
        out = pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for table, rows in g.items():
            (out / f"{table}.jsonl").write_text(
                "\n".join(json.dumps(r, default=str) for r in rows))
        print(f"\n  wrote {len(g)} graph tables -> {out}")
        for t, r in sorted(g.items(), key=lambda x: -len(x[1])):
            print(f"    {t:<18} {len(r):>6}")
    else:
        write_spanner(g, args.instance, args.database, args.project,
                      truncate=not args.append)


if __name__ == "__main__":
    main()
