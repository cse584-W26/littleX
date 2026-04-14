#!/usr/bin/env bash
set -euo pipefail
NAMESPACE="default"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Killing any port-forward on :8081..."
lsof -ti:8081 2>/dev/null | xargs -r kill 2>/dev/null || true

echo "Deleting manifest..."
kubectl delete -n "$NAMESPACE" -f "$MANIFEST" --ignore-not-found

echo "Deleting ConfigMap..."
kubectl delete configmap littlex-neo4j-http-src -n "$NAMESPACE" --ignore-not-found

echo "Done."
