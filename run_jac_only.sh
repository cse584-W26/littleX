#!/usr/bin/env bash
# Re-runs ONLY the two Jac baselines (filter_pushdown + naive_sam=topology-index-unleased)
# and writes into the existing overnight_runs/20260414_021902/ dir so the
# successful SQL/Neo4j CSVs are preserved.
set +e
set -u

ROOT="/home/savini98/repos/littleX"
OUT_ROOT="$ROOT/overnight_runs/20260414_021902"
LOG="$OUT_ROOT/run_jac.log"
BENCH="$ROOT/littleX-benchmarks/bench_own_tweets_selectivity.py"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
RUNS_PER_CONFIG=5

mkdir -p "$OUT_ROOT"
touch "$LOG"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
hdr() { log ""; log "================================================================"; log "  $*"; log "================================================================"; }

kill_port() {
    local port="$1"
    if lsof -ti:"$port" &>/dev/null; then
        kill $(lsof -ti:"$port") 2>/dev/null || true
        sleep 1
    fi
}

full_teardown() {
    log "Teardown: kubectl delete all --all (namespace default)"
    kubectl delete all --all -n default --wait=true --timeout=120s >>"$LOG" 2>&1 || true
    kubectl delete pvc --all -n default --wait=true --timeout=60s >>"$LOG" 2>&1 || true
    kubectl delete configmap --all -n default >>"$LOG" 2>&1 || true
    kubectl delete secret --all -n default >>"$LOG" 2>&1 || true
    kill_port 8080
    kill_port 8081
    pkill -f "jac start" 2>/dev/null || true
    sleep 8
}

set_jac_branch() {
    local branch="$1"
    local toml="$ROOT/littleX_FULLSTACK/jac.toml"
    log "Setting jaseci_branch = \"$branch\" in jac.toml"
    "$PYTHON" - "$toml" "$branch" <<'PY'
import sys, re
p, branch = sys.argv[1], sys.argv[2]
s = open(p).read()
s2 = re.sub(r'jaseci_branch\s*=\s*"[^"]*"', f'jaseci_branch = "{branch}"', s)
open(p, "w").write(s2)
PY
}

wait_for_http() {
    local url="$1" max_tries="${2:-60}"
    for i in $(seq 1 "$max_tries"); do
        if curl -s -o /dev/null --max-time 2 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}

run_bench() {
    local out_dir="$1" url="$2" prefix="$3" auth="$4"
    mkdir -p "$out_dir"
    log "Benchmark -> $url  prefix=$prefix  auth=$auth"
    "$PYTHON" "$BENCH" \
        -u "$url" \
        -r "$RUNS_PER_CONFIG" \
        --endpoint-prefix "$prefix" \
        --auth-scheme "$auth" \
        --output "$out_dir" \
        >>"$LOG" 2>&1
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        log "BENCH FAILED (exit $rc)"
    else
        log "Benchmark wrote $out_dir/results.csv"
    fi
    return $rc
}

run_jac() {
    local label="$1" branch="$2"
    hdr "Baseline: Jac ($label) — jaseci branch '$branch'"
    full_teardown
    set_jac_branch "$branch"
    cd "$ROOT/littleX_FULLSTACK"
    log "Deploying FULLSTACK on branch $branch..."
    ./deploy.sh >>"$LOG" 2>&1 &
    local deploy_pid=$!
    if ! wait_for_http "http://localhost:8080/docs" 240; then
        log "FULLSTACK did not come up — skipping bench"
        kill $deploy_pid 2>/dev/null || true
        return
    fi
    run_bench "$OUT_ROOT/jac_$label" "http://localhost:8080" "/walker" "jwt"
    kill $deploy_pid 2>/dev/null || true
}

hdr "Jac-only rerun  —  out: $OUT_ROOT"

run_jac filter_pushdown  filter_pushdown
run_jac naive_sam        topology-index-unleased

hdr "Done"
full_teardown
for d in "$OUT_ROOT"/jac_*/; do
    if [ -f "$d/results.csv" ]; then
        log "  $(basename $d): OK"
    else
        log "  $(basename $d): MISSING"
    fi
done
