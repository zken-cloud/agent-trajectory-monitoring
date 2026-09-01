#!/usr/bin/env bash
# Full offline verification. No GCP project required. ~60s.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-./.venv/bin/python}"; [ -x "$PY" ] || PY=python3
step(){ printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "1/7 seed data";        $PY data/make_seed.py
step "2/7 capture test";     $PY -W ignore tests/test_capture.py 2>/dev/null | tail -8
step "3/7 corpus + attacks"
  rm -rf corpus attack_out
  $PY -W ignore traffic/generate.py --count 2000 --out ./corpus >/dev/null 2>&1
  $PY -W ignore redteam/attacks.py --run all --scripted --enforcement shadow --out ./attack_out >/dev/null 2>&1
  echo "  $(wc -l < corpus/trajectory_sessions.jsonl) benign + $(wc -l < attack_out/trajectory_sessions.jsonl) attack sessions"
step "4/7 detection SQL";    $PY sql/validate.py --corpus ./corpus --attacks ./attack_out
step "5/7 graph model"
  rm -rf graph_out
  $PY spanner/etl.py --source ./corpus,./attack_out --out ./graph_out --sessions 300 | head -3
  $PY spanner/validate_graph.py --graph ./graph_out
step "6/7 dashboard render"; $PY tests/test_dashboard.py
step "7/7 terraform";        (cd terraform && terraform validate)
printf '\n\033[1;32mALL CHECKS PASSED\033[0m\n'
