#!/usr/bin/env bash
# fan_out=2000 sweep across all 5 baselines. Larger fan-out surfaces the
# O(n) Naive SAM scan vs O(1) New SAM/FP lookup difference that was
# invisible at fan_out=200. Correctness assertion enabled in bench driver.
#
# Order: SQL/Neo4j first (fast, stable), then Jac branches last (long
# deploys). Output dir: overnight_runs/fanout_2000_<ts>/
set +e
set -u

ROOT="/home/savini98/repos/littleX"
TS=$(date +%Y%m%d_%H%M%S)
OUT_ROOT="$ROOT/overnight_runs/fanout_2000_$TS"
LOG="$OUT_ROOT/run.log"
BENCH="$ROOT/littleX-benchmarks/bench_own_tweets_selectivity.py"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
RUNS_PER_CONFIG=5
WARMUP=20

mkdir -p "$OUT_ROOT"; touch "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
hdr() { log ""; log "================================================================"; log "  $*"; log "================================================================"; }

kill_port() { if lsof -ti:"$1" &>/dev/null; then kill $(lsof -ti:"$1") 2>/dev/null || true; sleep 1; fi; }

full_teardown() {
    log "Teardown"
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
        curl -s -o /dev/null --max-time 2 "$url" >/dev/null 2>&1 && return 0
        sleep 3
    done
    return 1
}

run_bench() {
    local out_dir="$1" url="$2" prefix="$3" auth="$4"
    mkdir -p "$out_dir"
    log "Bench -> $url prefix=$prefix auth=$auth warmup=$WARMUP"
    "$PYTHON" "$BENCH" \
        -u "$url" -r "$RUNS_PER_CONFIG" \
        --endpoint-prefix "$prefix" --auth-scheme "$auth" --warmup "$WARMUP" \
        --output "$out_dir" >>"$LOG" 2>&1
    local rc=$?
    [ "$rc" -ne 0 ] && log "BENCH FAILED (exit $rc)" || log "Wrote $out_dir/results.csv"
    return $rc
}

run_sql_neo4j_baseline() {
    local label="$1" deploy_cmd="$2" url="$3" prefix="$4" auth="$5"
    hdr "$label"
    full_teardown
    eval "$deploy_cmd" >>"$LOG" 2>&1
    if wait_for_http "$url/walker/clear_data" 60; then
        run_bench "$OUT_ROOT/$label" "$url" "$prefix" "$auth"
    else
        log "$label did not come up"
    fi
}

run_jac() {
    local label="$1" branch="$2"
    hdr "Jac $label — branch '$branch'"
    full_teardown
    set_jac_branch "$branch"
    cd "$ROOT/littleX_FULLSTACK"
    log "Deploying..."
    ./deploy.sh >>"$LOG" 2>&1 &
    local deploy_pid=$!
    if ! wait_for_http "http://localhost:8080/docs" 240; then
        log "Deploy timeout"; kill $deploy_pid 2>/dev/null || true; return
    fi
    run_bench "$OUT_ROOT/$label" "http://localhost:8080" "/walker" "jwt"
    kill $deploy_pid 2>/dev/null || true
}

hdr "fan_out=2000 sweep across 5 baselines"

# --- SQL / Neo4j ---
run_sql_neo4j_baseline "pg_handtuned" \
    "cd $ROOT/littleX_postgres_handtuned && ./deploy_handtuned_k8s.sh" \
    "http://localhost:8080" "/walker" "bearer-username"

run_sql_neo4j_baseline "sqlalchemy_pure" \
    "cd $ROOT/littleX_sqlalchemy && ./deploy_sqlalchemy_k8s.sh" \
    "http://localhost:8080" "/walker" "bearer-username"

run_sql_neo4j_baseline "neo4j_http" \
    "cd $ROOT/littleX_neo4j_http && ./deploy_neo4j_http_k8s.sh" \
    "http://localhost:8081" "/walker" "bearer-username"

# --- Jac ---
run_jac "jac_filter_pushdown" "filter_pushdown"
run_jac "jac_naive_sam"       "topology-index-unleased"

hdr "Done"
full_teardown
for d in "$OUT_ROOT"/*/; do
    [ -f "$d/results.csv" ] && log "  $(basename $d): OK" || log "  $(basename $d): MISSING"
done
log "Root: $OUT_ROOT"
