#!/usr/bin/env bash
# Re-run Neo4j HTTP with the aggregated collect(t{...}) Cypher query.
# Waits for run_jac_fair_eval_v2.sh to finish, then does a single
# Neo4j deploy+bench+teardown.
set +e
set -u

ROOT="/home/savini98/repos/littleX"
OUT_ROOT="$ROOT/overnight_runs/20260414_021902"
LOG="$OUT_ROOT/run_neo4j_rerun.log"
BENCH="$ROOT/littleX-benchmarks/bench_own_tweets_selectivity.py"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
RUNS_PER_CONFIG=5
WARMUP=20

mkdir -p "$OUT_ROOT"; touch "$LOG"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

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

wait_for_http() {
    local url="$1" max_tries="${2:-60}"
    for i in $(seq 1 "$max_tries"); do
        curl -s -o /dev/null --max-time 2 "$url" >/dev/null 2>&1 && return 0
        sleep 3
    done
    return 1
}

# Wait for Jac v2 to finish
while pgrep -f run_jac_fair_eval_v2.sh >/dev/null 2>&1; do
    log "Waiting for run_jac_fair_eval_v2.sh..."
    sleep 20
done

log "Starting Neo4j HTTP rerun with collect(t{...}) aggregation"
full_teardown
cd "$ROOT/littleX_neo4j_http"
./deploy_neo4j_http_k8s.sh >>"$LOG" 2>&1
if wait_for_http "http://localhost:8081/walker/clear_data" 60; then
    mkdir -p "$OUT_ROOT/neo4j_http_aggregated"
    "$PYTHON" "$BENCH" \
        -u "http://localhost:8081" -r "$RUNS_PER_CONFIG" \
        --endpoint-prefix "/walker" --auth-scheme "bearer-username" --warmup "$WARMUP" \
        --output "$OUT_ROOT/neo4j_http_aggregated" >>"$LOG" 2>&1
    log "Wrote $OUT_ROOT/neo4j_http_aggregated/results.csv"
else
    log "Neo4j HTTP did not come up"
fi

full_teardown
log "Done"
