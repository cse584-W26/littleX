#!/usr/bin/env bash
# Deploy littleX_neo4j_http (FastAPI + Neo4j driver + Neo4j pod) into the
# same namespace as the other backends, then port-forward on localhost:8081.
#
# Mirrors deploy_handtuned_k8s.sh structure.
set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=littlex-neo4j-http"
TIMEOUT=300
LOCAL_HTTP_PORT=8081
CONFIGMAP_NAME="littlex-neo4j-http-src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Checking for existing deployment..."
EXISTING=$(kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null | wc -l)
if [ "$EXISTING" -gt 0 ]; then
    echo "Deployment already running. Run ./teardown_neo4j_http_k8s.sh first."
    kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
    exit 1
fi

echo "Creating source ConfigMap '$CONFIGMAP_NAME'..."
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1
kubectl create configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" \
    --from-file=requirements.txt="$SCRIPT_DIR/requirements.txt" \
    --from-file=src__init__.py="$SCRIPT_DIR/src/__init__.py" \
    --from-file=src_routes__init__.py="$SCRIPT_DIR/src/routes/__init__.py" \
    --from-file=src_routes_user.py="$SCRIPT_DIR/src/routes/user.py" \
    --from-file=src_routes_walker.py="$SCRIPT_DIR/src/routes/walker.py"

echo "Applying $MANIFEST..."
kubectl apply -n "$NAMESPACE" -f "$MANIFEST"

echo "Waiting for Neo4j pod..."
kubectl wait pod -n "$NAMESPACE" -l "$APP_LABEL,component=neo4j" \
    --for=condition=Ready --timeout="${TIMEOUT}s"

echo "Waiting for FastAPI pod (pip-install takes ~30s)..."
if ! kubectl wait pod -n "$NAMESPACE" -l "$APP_LABEL,component=fastapi" \
        --for=condition=Ready --timeout="${TIMEOUT}s" 2>/dev/null; then
    echo "FastAPI pod did not become Ready. Logs:" >&2
    kubectl logs -n "$NAMESPACE" -l "$APP_LABEL,component=fastapi" -c pip-install --tail=80 || true
    kubectl logs -n "$NAMESPACE" -l "$APP_LABEL,component=fastapi" -c fastapi --tail=80 || true
    exit 1
fi

echo ""
kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
echo ""
echo "Port-forward on localhost:${LOCAL_HTTP_PORT}..."

if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null || true
    sleep 1
fi

kubectl port-forward svc/littlex-neo4j-http ${LOCAL_HTTP_PORT}:8000 -n "$NAMESPACE" &
sleep 2

echo "FastAPI reachable at http://localhost:${LOCAL_HTTP_PORT}"
echo ""
echo "Run:"
echo "  cd ../littleX-benchmarks"
echo "  python bench_own_tweets_selectivity.py -u http://localhost:${LOCAL_HTTP_PORT} \\"
echo "      --endpoint-prefix '' --auth-scheme bearer-username \\"
echo "      --output Neo4j_HTTP/own_tweets"
