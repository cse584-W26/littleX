#!/usr/bin/env bash
# After the current run_jac_optimized.sh finishes, deploy Jac FP once more
# and run the jid-only probe. Tests whether the 4.5ms/tweet slope is
# per-attribute anchor hydration (then probe is much faster) or envelope
# serialization (then probe is just as slow).
set +e
set -u

ROOT="/home/savini98/repos/littleX"
OUT_ROOT="$ROOT/overnight_runs/20260414_021902"
LOG="$OUT_ROOT/run_jid_probe.log"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
PROBE="$ROOT/littleX-benchmarks/bench_jid_only_probe.py"

mkdir -p "$OUT_ROOT"; touch "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Wait for the prior optimized rerun to finish
while pgrep -f run_jac_optimized.sh >/dev/null 2>&1; do
    log "Waiting for run_jac_optimized.sh to finish..."
    sleep 20
done

log "Prior run finished. Starting jid-only probe on FP branch..."

kill_port() { if lsof -ti:"$1" &>/dev/null; then kill $(lsof -ti:"$1") 2>/dev/null || true; sleep 1; fi; }

log "Teardown"
kubectl delete all --all -n default --wait=true --timeout=120s >>"$LOG" 2>&1 || true
kubectl delete pvc --all -n default --wait=true --timeout=60s >>"$LOG" 2>&1 || true
kubectl delete configmap --all -n default >>"$LOG" 2>&1 || true
kubectl delete secret --all -n default >>"$LOG" 2>&1 || true
kill_port 8080; kill_port 8081
pkill -f "jac start" 2>/dev/null || true
sleep 8

log "Setting branch = filter_pushdown"
"$PYTHON" - "$ROOT/littleX_FULLSTACK/jac.toml" filter_pushdown <<'PY'
import sys, re
p, branch = sys.argv[1], sys.argv[2]
s = open(p).read()
open(p, "w").write(re.sub(r'jaseci_branch\s*=\s*"[^"]*"', f'jaseci_branch = "{branch}"', s))
PY

cd "$ROOT/littleX_FULLSTACK"
log "Deploying..."
./deploy.sh >>"$LOG" 2>&1 &
deploy_pid=$!

for i in $(seq 1 80); do
    curl -s -o /dev/null --max-time 2 http://localhost:8080/docs && break
    sleep 3
done

log "Running jid-only probe..."
"$PYTHON" "$PROBE" -u http://localhost:8080 -r 5 --warmup 20 \
    --output "$OUT_ROOT/jac_fp_jid_only" >>"$LOG" 2>&1
log "Probe exit: $?"

kill $deploy_pid 2>/dev/null || true
log "Teardown"
kubectl delete all --all -n default --wait=true --timeout=120s >>"$LOG" 2>&1 || true
kill_port 8080
pkill -f "jac start" 2>/dev/null || true
log "Done"
