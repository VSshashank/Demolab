#!/usr/bin/env bash
# Starts the backend and the telemetry simulator together, from a cold clone.
#
#   ./run_demo.sh              live simulator, 6 vehicles
#   ./run_demo.sh --replay     replay the committed golden run instead
#   ./run_demo.sh --fresh      wipe the database first
#
# Creates the virtualenv and installs pinned dependencies on first run.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
VEHICLES="${VEHICLES:-6}"
SEED="${SEED:-42}"
VENV=".venv"
REPLAY=0
FRESH=0

for arg in "$@"; do
  case "$arg" in
    --replay) REPLAY=1 ;;
    --fresh)  FRESH=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[2m  %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- environment
if [ ! -d "$VENV" ]; then
  echo "creating virtualenv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  echo "installing pinned dependencies (this is the slow part, once)"
  "$VENV/bin/pip" install --quiet -r requirements.txt
fi
PY="$VENV/bin/python"

# Versions are pinned because a joblib unpickle across scikit-learn releases
# fails loudly, and it fails on the delivery machine rather than here.
if ! "$PY" -c "import fastapi, sklearn, xgboost" 2>/dev/null; then
  echo "dependencies missing or mismatched, reinstalling"
  "$VENV/bin/pip" install --quiet -r requirements.txt
fi

mkdir -p data
[ "$FRESH" = "1" ] && { say "clearing data/emission_monitor.db"; rm -f data/emission_monitor.db*; }

if [ ! -f ml/models/xgboost_model.joblib ]; then
  echo
  echo "  ml/models/xgboost_model.joblib is missing."
  echo "  The dashboard will run, but every prediction will be marked"
  echo "  physics_fallback and the Models tab will be empty."
  echo "  To train: $PY -m ml.generate_dataset --hours 200 && $PY -m ml.train_models"
  echo
fi

# ------------------------------------------------------------------- shutdown
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo
  say "stopped"
}
trap cleanup EXIT INT TERM

# -------------------------------------------------------------------- backend
echo
printf '\033[1mFleetCarbon\033[0m  starting backend on http://localhost:%s\n' "$PORT"
"$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" --log-level info &
PIDS+=($!)

for _ in $(seq 1 40); do
  if "$PY" - "$PORT" <<'PROBE' 2>/dev/null; then break; fi
import socket, sys
s = socket.socket(); s.settimeout(0.4)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PROBE
  sleep 0.3
done

# ------------------------------------------------------------------ simulator
if [ "$REPLAY" = "1" ]; then
  if [ ! -s data/recorded_run.jsonl ]; then
    echo "no golden run at data/recorded_run.jsonl; run once without --replay first" >&2
    exit 1
  fi
  say "replaying data/recorded_run.jsonl"
  "$PY" -m simulator.run_simulator --replay data/recorded_run.jsonl \
        --sink "ws://localhost:$PORT/ws/ingest" &
else
  "$PY" -m simulator.run_simulator --vehicles "$VEHICLES" --seed "$SEED" \
        --sink "ws://localhost:$PORT/ws/ingest" &
fi
PIDS+=($!)

echo
printf '  dashboard  \033[96mhttp://localhost:%s\033[0m\n' "$PORT"
printf '  health     http://localhost:%s/api/health\n' "$PORT"
say "ctrl-c to stop both"
echo
wait
