#!/usr/bin/env bash
# Unattended overnight run of the fan_out=200 own-tweets selectivity sweep
# across all five baselines.
#
# For each baseline:
#   1. Tear down everything in the namespace (best-effort)
#   2. For Jac baselines: rewrite jac.toml branch line
#   3. Deploy
#   4. Wait for readiness
#   5. Run bench_own_tweets_selectivity.py
#   6. Copy CSV into overnight_runs/<timestamp>/<baseline>/
#   7. Tear down
#
# All logs stream to overnight_runs/<timestamp>/run.log. Each baseline's
# failure is recorded but does NOT stop the run — we want as much data
# as possible when we wake up.
#
# Run once, approve bash perms once, close laptop.
#
# Assumptions:
#   * kubectl context is set to the right cluster
#   * `jac` CLI is on PATH
#   * ports 8080 / 8081 / 8090 free to use for port-forwards
#   * Namespace "default" is the working namespace for all baselines

set +e  # don't stop on any single failure
set -u

ROOT="/home/savini98/repos/littleX"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_ROOT="$ROOT/overnight_runs/$TS"
LOG="$OUT_ROOT/run.log"
BENCH="$ROOT/littleX-benchmarks/bench_own_tweets_selectivity.py"
PYTHON="/home/savini98/miniconda3/envs/db-main/bin/python"
RUNS_PER_CONFIG=5

mkdir -p "$OUT_ROOT"
touch "$LOG"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
hdr() { log ""; log "================================================================"; log "  $*"; log "================================================================"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    kill_port 8090
    # Give the cluster a moment to settle
    sleep 5
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
        log "BENCH FAILED (exit $rc) — continuing with next baseline"
    else
        log "Benchmark wrote $out_dir/results.csv"
    fi
    return $rc
}

set_jac_branch() {
    local branch="$1"
    local toml="$ROOT/littleX_FULLSTACK/jac.toml"
    log "Setting jaseci_branch = \"$branch\" in jac.toml"
    # macOS/BSD sed-safe: use a portable in-place substitution
    python3 - "$toml" "$branch" <<'PY'
import sys, re
p, branch = sys.argv[1], sys.argv[2]
s = open(p).read()
s2 = re.sub(r'jaseci_branch\s*=\s*"[^"]*"',
            f'jaseci_branch = "{branch}"', s)
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

# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

run_pg_handtuned() {
    hdr "Baseline: PG hand-tuned"
    full_teardown
    cd "$ROOT/littleX_postgres_handtuned"
    log "Deploying PG hand-tuned..."
    ./deploy_handtuned_k8s.sh >>"$LOG" 2>&1
    if ! wait_for_http "http://localhost:8080/walker/clear_data" 60; then
        log "PG handtuned did not come up — skipping bench"
        return
    fi
    run_bench "$OUT_ROOT/pg_handtuned" "http://localhost:8080" "/walker" "bearer-username"
}

run_sqlalchemy_pure() {
    hdr "Baseline: SQLAlchemy pure"
    full_teardown
    cd "$ROOT/littleX_sqlalchemy"
    log "Deploying SQLAlchemy pure..."
    ./deploy_sqlalchemy_k8s.sh >>"$LOG" 2>&1
    if ! wait_for_http "http://localhost:8080/walker/clear_data" 60; then
        log "SQLAlchemy pure did not come up — skipping bench"
        return
    fi
    run_bench "$OUT_ROOT/sqlalchemy_pure" "http://localhost:8080" "/walker" "bearer-username"
}

run_neo4j_http() {
    hdr "Baseline: Neo4j HTTP"
    full_teardown
    cd "$ROOT/littleX_neo4j_http"
    log "Deploying Neo4j HTTP..."
    ./deploy_neo4j_http_k8s.sh >>"$LOG" 2>&1
    if ! wait_for_http "http://localhost:8081/walker/clear_data" 60; then
        log "Neo4j HTTP did not come up — skipping bench"
        return
    fi
    run_bench "$OUT_ROOT/neo4j_http" "http://localhost:8081" "/walker" "bearer-username"
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
    if ! wait_for_http "http://localhost:8080/docs" 180; then
        log "FULLSTACK did not come up — skipping bench"
        kill $deploy_pid 2>/dev/null || true
        return
    fi
    run_bench "$OUT_ROOT/jac_$label" "http://localhost:8080" "/walker" "jwt"
    kill $deploy_pid 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

hdr "Overnight selectivity sweep starting  —  out: $OUT_ROOT"

run_pg_handtuned
run_sqlalchemy_pure
run_neo4j_http
run_jac filter_pushdown   filter_pushdown
run_jac naive_sam         topology-index-unleased

hdr "All baselines complete"
full_teardown

log "Results under $OUT_ROOT/"
for d in "$OUT_ROOT"/*/; do
    if [ -f "$d/results.csv" ]; then
        log "  $(basename $d): OK"
    else
        log "  $(basename $d): MISSING"
    fi
done
log "Done. Full log at $LOG"
