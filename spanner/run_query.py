"""Run a named query from queries.gql against Spanner.

Exists because `gcloud spanner databases execute-sql` cannot bind query
parameters, and Q2/Q3 take @flagged_sessions - the scope that keeps the taint
query honest. Without binding they fail with:
    No parameter found for binding: flagged_sessions

    python spanner/run_query.py Q2 --database trajectory
    python spanner/run_query.py Q3 --database trajectory --scope findings.json
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
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def extract(name: str, text: str) -> str:
    """Comments are stripped BEFORE the statement is cut at its first ';'.

    The other way round looks equivalent and is not: the prose in Q4's header
    contains a semicolon, so a `/^-- Q4/,/;/` range ends inside the comment and
    yields an empty query - which Spanner reports as the very unhelpful
    "No value provided for required field: sql".
    """
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(rf"^-- {name} ", l)]
    if not starts:
        raise SystemExit(f"query {name} not found in queries.gql")
    body = [l for l in lines[starts[0]:] if not l.strip().startswith("--")]
    out: list[str] = []
    for line in body:
        out.append(line)
        if ";" in line:
            break
    return "\n".join(out).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Q1..Q6")
    ap.add_argument("--project", default=None)
    ap.add_argument("--instance", default="trajectory-graph")
    ap.add_argument("--database", default="trajectory")
    ap.add_argument("--scope", default=None,
                    help="JSON array / newline list of session_ids for "
                         "@flagged_sessions. Default: every session in the graph.")
    args = ap.parse_args()

    sql = extract(args.query, (ROOT / "queries.gql").read_text())

    from google.cloud import spanner
    client = spanner.Client(project=args.project) if args.project else spanner.Client()
    db = client.instance(args.instance).database(args.database)

    params, types_ = {}, {}
    if "@flagged_sessions" in sql:
        if args.scope:
            raw = pathlib.Path(args.scope).read_text().strip()
            ids = json.loads(raw) if raw.startswith("[") else raw.split()
        else:
            with db.snapshot() as snap:
                ids = [r[0] for r in snap.execute_sql("SELECT session_id FROM Session")]
        from google.cloud.spanner_v1 import param_types
        params["flagged_sessions"] = ids
        types_["flagged_sessions"] = param_types.Array(param_types.STRING)
        print(f"  scope: {len(ids)} sessions")

    with db.snapshot() as snap:
        rows = list(snap.execute_sql(sql, params=params or None,
                                     param_types=types_ or None))
    for r in rows:
        print("  " + "  ".join(str(v) for v in r))
    print(f"\n  {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
