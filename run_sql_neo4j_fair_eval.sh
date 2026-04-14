#!/usr/bin/env bash
# After run_jac_fair_eval.sh finishes, redeploy and bench the 3 non-Jac
# baselines (PG, SQLAlchemy pure, Neo4j HTTP) with the newly-instrumented
# /load_own_tweets endpoints that return server-side ms_traversal.
#
# Results go to overnight_runs/20260414_021902/{pg,sqlalchemy_pure,neo4j_http}_fair/
# alongside the existing client-only CSVs. Combined with jac_{fp,naive}_fair
# from the prior run, this gives the full 5-baseline Panel-A (server-timed)
# and Panel-B (client-timed) dataset.
set +e
set -u

ROOT="/home/savini98/repos/littleX"
OUT_ROOT="$ROOT/overnight_runs/20260414_021902"
LOG="$OUT_ROOT/run_sql_neo4j_fair_eval.log"
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

wait_for_http() {
    local url="$1" max_tries="${2:-60}"
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

# Wait for the prior Jac fair-eval to finish
while pgrep -f run_jac_fair_eval.sh >/dev/null 2>&1; do
    log "Waiting for run_jac_fair_eval.sh..."
    sleep 20
done
log "Prior Jac fair-eval finished. Starting SQL/Neo4j fair-eval reruns."

# --- PG hand-tuned ---
hdr "PG hand-tuned (fair eval — now with ms_traversal)"
full_teardown
cd "$ROOT/littleX_postgres_handtuned"
./deploy_handtuned_k8s.sh >>"$LOG" 2>&1
if wait_for_http "http://localhost:8080/walker/clear_data" 60; then
    run_bench "$OUT_ROOT/pg_handtuned_fair" "http://localhost:8080" "/walker" "bearer-username"
else
    log "PG did not come up"
fi

# --- SQLAlchemy pure ---
hdr "SQLAlchemy pure (fair eval)"
full_teardown
cd "$ROOT/littleX_sqlalchemy"
./deploy_sqlalchemy_k8s.sh >>"$LOG" 2>&1
if wait_for_http "http://localhost:8080/walker/clear_data" 60; then
    run_bench "$OUT_ROOT/sqlalchemy_pure_fair" "http://localhost:8080" "/walker" "bearer-username"
else
    log "SQLAlchemy did not come up"
fi

# --- Neo4j HTTP ---
hdr "Neo4j HTTP (fair eval)"
full_teardown
cd "$ROOT/littleX_neo4j_http"
./deploy_neo4j_http_k8s.sh >>"$LOG" 2>&1
if wait_for_http "http://localhost:8081/walker/clear_data" 60; then
    run_bench "$OUT_ROOT/neo4j_http_fair" "http://localhost:8081" "/walker" "bearer-username"
else
    log "Neo4j HTTP did not come up"
fi

hdr "Done"
full_teardown
for d in "$OUT_ROOT"/*_fair/; do
    [ -f "$d/results.csv" ] && log "  $(basename $d): OK" || log "  $(basename $d): MISSING"
done
