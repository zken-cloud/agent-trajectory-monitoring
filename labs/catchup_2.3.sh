#!/usr/bin/env bash
# Fast-forward to the start of Lab 2.3 from ANY prior state. Use it without
# hesitation - the labs are decoupled so a broken earlier lab costs you nothing.
source "$(dirname "$0")/_common.sh"
seed
corpus; attacks
echo "ready for Lab 2.3: corpus/ + attack_out/ present (graph load: see terraform/)"
