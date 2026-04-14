#!/usr/bin/env bash
set -euo pipefail
NAMESPACE="default"
APP_LABEL="app=littlex-handtuned"
LOCAL_HTTP_PORT=8080
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

# Kill any port-forward on 8080
if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null || true
fi

kubectl delete pod -n "$NAMESPACE" -l "$APP_LABEL" --grace-period=1 --ignore-not-found 2>/dev/null || true
kubectl delete -f "$MANIFEST" --ignore-not-found --grace-period=5 2>/dev/null || true
kubectl delete configmap littlex-handtuned-src -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
echo "handtuned torn down"
