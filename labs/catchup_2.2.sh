#!/usr/bin/env bash
# Fast-forward to the start of Lab 2.2 from ANY prior state. Use it without
# hesitation - the labs are decoupled so a broken earlier lab costs you nothing.
source "$(dirname "$0")/_common.sh"
seed
attacks
echo "ready for Lab 2.2: attack_out/ has $(wc -l < attack_out/trajectory_sessions.jsonl) sessions"
