#!/usr/bin/env bash
# Re-runs the two Jac baselines with:
#   - denormalized likers on Tweet node (no per-tweet incoming-edge traversal)
#   - 20 warmup requests per config (isolate steady-state from cold-start)
# Writes into overnight_runs/20260414_021902/jac_{fp,naive}_optimized/
# alongside the existing CSVs for side-by-side comparison.
set +e
set -u

ROOT="/home/savini98/repos/littleX"
OUT_ROOT="$ROOT/overnight_runs/20260414_021902"
LOG="$OUT_ROOT/run_jac_optimized.log"
BENCH="$ROOT/littleX-benchmarks/bench_own_tweets_selectivity.py"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
RUNS_PER_CONFIG=5
WARMUP=20

mkdir -p "$OUT_ROOT"
touch "$LOG"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
hdr() { log ""; log "================================================================"; log "  $*"; log "================================================================"; }

kill_port() { if lsof -ti:"$1" &>/dev/null; then kill $(lsof -ti:"$1") 2>/dev/null || true; sleep 1; fi; }

full_teardown() {
    log "Teardown: kubectl delete all --all (namespace default)"
    kubectl delete all --all -n default --wait=true --timeout=120s >>"$LOG" 2>&1 || true
    kubectl delete pvc --all -n default --wait=true --timeout=60s >>"$LOG" 2>&1 || true
    kubectl delete configmap --all -n default >>"$LOG" 2>&1 || true
    kubectl delete secret --all -n default >>"$LOG" 2>&1 || true
    kill_port 8080; kill_port 8081
    pkill -f "jac start" 2>/dev/null || true
    sleep 8
}

set_jac_branch() {
    local branch="$1" toml="$ROOT/littleX_FULLSTACK/jac.toml"
    log "Setting jaseci_branch = \"$branch\""
    "$PYTHON" - "$toml" "$branch" <<'PY'
import sys, re
p, branch = sys.argv[1], sys.argv[2]
s = open(p).read()
open(p, "w").write(re.sub(r'jaseci_branch\s*=\s*"[^"]*"', f'jaseci_branch = "{branch}"', s))
PY
}

wait_for_http() {
    local url="$1" max_tries="${2:-80}"
    for i in $(seq 1 "$max_tries"); do
        if curl -s -o /dev/null --max-time 2 "$url" >/dev/null 2>&1; then return 0; fi
        sleep 3
    done
    return 1
}

run_bench() {
    local out_dir="$1"
    mkdir -p "$out_dir"
    log "Benchmark -> http://localhost:8080  prefix=/walker  auth=jwt  warmup=$WARMUP"
    "$PYTHON" "$BENCH" \
        -u "http://localhost:8080" \
        -r "$RUNS_PER_CONFIG" \
        --endpoint-prefix "/walker" \
        --auth-scheme "jwt" \
        --warmup "$WARMUP" \
        --output "$out_dir" \
        >>"$LOG" 2>&1
    local rc=$?
    [ "$rc" -ne 0 ] && log "BENCH FAILED (exit $rc)" || log "Wrote $out_dir/results.csv"
    return $rc
}

run_jac() {
    local label="$1" branch="$2"
    hdr "Baseline: Jac $label (optimized) — branch '$branch'"
    full_teardown
    set_jac_branch "$branch"
    cd "$ROOT/littleX_FULLSTACK"
    log "Deploying FULLSTACK on branch $branch..."
    ./deploy.sh >>"$LOG" 2>&1 &
    local deploy_pid=$!
    if ! wait_for_http "http://localhost:8080/docs" 240; then
        log "FULLSTACK did not come up — skipping"
        kill $deploy_pid 2>/dev/null || true
        return
    fi
    run_bench "$OUT_ROOT/jac_${label}_optimized"
    kill $deploy_pid 2>/dev/null || true
}

hdr "Jac optimized rerun  —  denormalized likers + ${WARMUP}-request warmup"

run_jac filter_pushdown  filter_pushdown
run_jac naive_sam        topology-index-unleased

hdr "Done"
full_teardown
for d in "$OUT_ROOT"/jac_*_optimized/; do
    [ -f "$d/results.csv" ] && log "  $(basename $d): OK" || log "  $(basename $d): MISSING"
done
