#!/usr/bin/env bash
# Fast-forward to the start of Lab 2.1 from ANY prior state. Use it without
# hesitation - the labs are decoupled so a broken earlier lab costs you nothing.
source "$(dirname "$0")/_common.sh"
seed
rm -rf telemetry_out && TRAJECTORY_SINK=jsonl TRAJECTORY_OUT_DIR=./telemetry_out \
  "$PY" -W ignore tests/test_capture.py >/dev/null 2>&1 || true
echo "ready for Lab 2.1: telemetry_out/ seeded"
