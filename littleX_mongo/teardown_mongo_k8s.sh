#!/usr/bin/env bash
#
# Tear down the littleX_mongo deployment (Flask + MongoDB) from Kubernetes
# and release the port-forward.
#
# Usage:
#   ./teardown_mongo_k8s.sh

set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=littlex-mongo"
LOCAL_HTTP_PORT=8090
CONFIGMAP_NAME="littlex-mongo-src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Tearing down littleX_mongo deployment..."

# Kill port-forward if running
if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    echo "  Releasing port-forward on :${LOCAL_HTTP_PORT}..."
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null || true
fi

# Delete k8s resources
kubectl delete -n "$NAMESPACE" -f "$MANIFEST" --ignore-not-found

# Delete the ConfigMap
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --ignore-not-found

echo "Done. Remaining pods with label $APP_LABEL:"
kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null || echo "  (none)"
