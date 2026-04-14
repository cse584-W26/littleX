#!/usr/bin/env bash
# Deploy littleX_postgres_handtuned (Flask + psycopg + tuned Postgres) into
# the same Kubernetes namespace where jac-scale runs, then port-forward on
# localhost:8080 so benchmarks can hit it via --url. Mirrors the SQLAlchemy
# variant's deploy script.
set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=littlex-handtuned"
TIMEOUT=300
LOCAL_HTTP_PORT=8080
CONFIGMAP_NAME="littlex-handtuned-src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Checking for existing littleX_handtuned deployment..."
EXISTING=$(kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null | wc -l)
if [ "$EXISTING" -gt 0 ]; then
    echo "littleX_handtuned deployment already running. Run teardown first."
    kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
    exit 1
fi

echo "Creating source ConfigMap '$CONFIGMAP_NAME' from local files..."
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1
kubectl create configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" \
    --from-file=requirements.txt="$SCRIPT_DIR/requirements.txt" \
    --from-file=src__init__.py="$SCRIPT_DIR/src/__init__.py" \
    --from-file=src_db.py="$SCRIPT_DIR/src/db.py" \
    --from-file=src_schema.sql="$SCRIPT_DIR/src/schema.sql" \
    --from-file=src_routes_user.py="$SCRIPT_DIR/src/routes/user.py" \
    --from-file=src_routes_walker.py="$SCRIPT_DIR/src/routes/walker.py"

echo "Applying $MANIFEST to namespace '$NAMESPACE'..."
kubectl apply -n "$NAMESPACE" -f "$MANIFEST"

echo "Waiting for postgres pod to become Ready..."
kubectl wait pod -n "$NAMESPACE" -l "$APP_LABEL,component=postgres" \
    --for=condition=Ready --timeout="${TIMEOUT}s"

echo "Waiting for flask pod to become Ready (initContainer pip-install ~30s)..."
kubectl wait pod -n "$NAMESPACE" -l "$APP_LABEL,component=flask" \
    --for=condition=Ready --timeout="${TIMEOUT}s"

echo "Deployment ready!"
kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"

if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null || true
    sleep 1
fi
kubectl port-forward svc/littlex-handtuned ${LOCAL_HTTP_PORT}:8000 -n "$NAMESPACE" &
sleep 2
echo "Flask reachable at http://localhost:${LOCAL_HTTP_PORT}"
