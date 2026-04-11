#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=jaseci"
TIMEOUT=180
LITTLEX_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Checking for existing deployment..."
EXISTING=$(kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null | wc -l)
if [ "$EXISTING" -gt 0 ]; then
    echo "Deployment already running:"
    kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL"
    echo "Run teardown.sh first if you want a fresh deployment."
    exit 1
fi

echo "No existing deployment found. Starting littleX SQLAlchemy (backend only)..."
cd "$LITTLEX_DIR"
jac start main.jac --no-dev --no_client --scale --experimental &
JAC_PID=$!

echo "Waiting for jaseci pod to appear..."
ELAPSED=0
until kubectl get pods -n "$NAMESPACE" -l "$APP_LABEL" --no-headers 2>/dev/null | grep -q .; do
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "Timed out waiting for pod to appear."
        kill "$JAC_PID" 2>/dev/null || true
        exit 1
    fi
done

echo "Pod found. Waiting for it to become Ready..."
if kubectl wait pod -n "$NAMESPACE" -l "$APP_LABEL" \
    --for=condition=Ready --timeout="${TIMEOUT}s" 2>/dev/null; then
    echo ""
    echo "Deployment ready!"
    kubectl get pods -n "$NAMESPACE"
else
    echo "Pod did not become Ready within ${TIMEOUT}s."
    kill "$JAC_PID" 2>/dev/null || true
    exit 1
fi
