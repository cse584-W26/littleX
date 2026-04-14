#!/usr/bin/env bash
#
# Deploy littleX_mongo (Flask + MongoDB) into the same Kubernetes namespace
# where jac-scale runs, then start a port-forward on localhost:8090 so the
# benchmark scripts in littleX-benchmarks/ can hit it via --url.
#
# Fairness setup: every backend (Jac, Neo4j, SQLAlchemy, MongoDB) takes
# the same network path:
#   bench host -> kubectl port-forward -> ClusterIP svc -> pod
# Latency differences reflect the backend, not deployment topology.
#
# Usage:
#   ./deploy_mongo_k8s.sh

set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=littlex-mongo"
TIMEOUT=300
LOCAL_HTTP_PORT=8090
CONFIGMAP_NAME="littlex-mongo-src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Checking for existing littleX_mongo deployment..."
EXISTING=$(kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null | wc -l)
if [ "$EXISTING" -gt 0 ]; then
    echo "littleX_mongo deployment already running:"
    kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
    echo "Run ./teardown_mongo_k8s.sh first if you want a fresh deployment."
    exit 1
fi

# ---------------------------------------------------------------------------
# Create the source ConfigMap. Keys are flat (no "/" in ConfigMap keys);
# the manifest's items[].path mappings reconstruct the directory tree inside
# the pod.
# ---------------------------------------------------------------------------
echo "Creating source ConfigMap '$CONFIGMAP_NAME' from local files..."
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1
kubectl create configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" \
    --from-file=requirements.txt="$SCRIPT_DIR/requirements.txt" \
    --from-file=src__init__.py="$SCRIPT_DIR/src/__init__.py" \
    --from-file=src_routes_user.py="$SCRIPT_DIR/src/routes/user.py" \
    --from-file=src_routes_walker.py="$SCRIPT_DIR/src/routes/walker.py"

echo "Applying $MANIFEST to namespace '$NAMESPACE'..."
kubectl apply -n "$NAMESPACE" -f "$MANIFEST"

echo "Waiting for MongoDB pod to become Ready..."
if ! kubectl wait pod -n "$NAMESPACE" \
        -l "$APP_LABEL,component=mongodb" \
        --for=condition=Ready --timeout="${TIMEOUT}s" 2>/dev/null; then
    echo "MongoDB pod did not become Ready within ${TIMEOUT}s. Logs:" >&2
    kubectl logs -n "$NAMESPACE" -l "$APP_LABEL,component=mongodb" --tail=50 || true
    exit 1
fi

echo "Waiting for Flask pod to become Ready (initContainer pip-install can take ~30s)..."
if ! kubectl wait pod -n "$NAMESPACE" \
        -l "$APP_LABEL,component=flask" \
        --for=condition=Ready --timeout="${TIMEOUT}s" 2>/dev/null; then
    echo "Flask pod did not become Ready within ${TIMEOUT}s. Logs:" >&2
    echo "--- pip-install initContainer:" >&2
    kubectl logs -n "$NAMESPACE" -l "$APP_LABEL,component=flask" -c pip-install --tail=80 || true
    echo "--- flask container:" >&2
    kubectl logs -n "$NAMESPACE" -l "$APP_LABEL,component=flask" -c flask --tail=80 || true
    exit 1
fi

echo ""
echo "Deployment ready!"
kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
echo ""
echo "Setting up port-forward on localhost:${LOCAL_HTTP_PORT}..."

# Free the port if something stale is on it
if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    echo "  Killing stale process on :${LOCAL_HTTP_PORT}"
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null || true
    sleep 1
fi

kubectl port-forward svc/littlex-mongo-flask ${LOCAL_HTTP_PORT}:8000 -n "$NAMESPACE" &
sleep 2

if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    echo "Flask (MongoDB backend) is reachable at http://localhost:${LOCAL_HTTP_PORT}"
    echo ""
    echo "Network path (matches jac-scale / SQLAlchemy / Neo4j):"
    echo "  bench host -> kubectl port-forward -> svc/littlex-mongo-flask -> flask pod"
    echo "                                                                    |"
    echo "                                                                    v"
    echo "                                                     svc/littlex-mongo -> mongodb pod"
    echo ""
    echo "Run benchmarks against http://localhost:${LOCAL_HTTP_PORT}, e.g.:"
    echo "  cd ../littleX-benchmarks"
    echo "  python bench_evaluation.py  -u http://localhost:${LOCAL_HTTP_PORT} -r 10"
    echo "  python bench_graph_ops.py   -u http://localhost:${LOCAL_HTTP_PORT} -r 5"
    echo "  python bench_concurrent.py  -u http://localhost:${LOCAL_HTTP_PORT}"
else
    echo "Port-forward up but :${LOCAL_HTTP_PORT} not yet bound — give it a few more seconds."
fi
