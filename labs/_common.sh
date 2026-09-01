set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
seed()   { [ -f data/seed/customers.json ] || "$PY" data/make_seed.py; }
corpus() { [ -d ./corpus ] || "$PY" -W ignore traffic/generate.py --count "${1:-2000}" --out ./corpus; }
attacks(){ [ -d ./attack_out ] || "$PY" -W ignore redteam/attacks.py --run all --scripted \
             --enforcement shadow --out ./attack_out; }
