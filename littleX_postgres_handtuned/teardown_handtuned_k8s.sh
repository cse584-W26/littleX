#!/usr/bin/env bash
#
# Tear down the littleX_postgres_handtuned K8s deployment created by
# deploy_handtuned_k8s.sh and stop the local port-forward. Idempotent.
#
# Style mirrors teardown_sqlalchemy_k8s.sh and teardown_neo4j_k8s.sh —
# scoped to littlex-handtuned resources only, never nukes the namespace.

set -euo pipefail

NAMESPACE="default"
APP_LABEL="app=littlex-handtuned"
LOCAL_HTTP_PORT=8080
CONFIGMAP_NAME="littlex-handtuned-src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$SCRIPT_DIR/k8s/manifest.yaml"

echo "Tearing down littleX_postgres_handtuned deployment..."

if [ -f "$MANIFEST" ]; then
    kubectl delete -n "$NAMESPACE" -f "$MANIFEST" --ignore-not-found 2>/dev/null \
        && echo "  Deleted Postgres + Flask resources" \
        || echo "  Nothing to delete"
else
    kubectl delete deployment,statefulset,svc,configmap -n "$NAMESPACE" -l "$APP_LABEL" --ignore-not-found 2>/dev/null \
        && echo "  Deleted resources by label" \
        || echo "  Nothing to delete"
fi

# The src ConfigMap is created out-of-band by deploy_handtuned_k8s.sh, so
# the manifest delete above does NOT remove it.
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" --ignore-not-found 2>/dev/null \
    && echo "  Deleted source ConfigMap '$CONFIGMAP_NAME'" \
    || true

if lsof -ti:${LOCAL_HTTP_PORT} &>/dev/null; then
    kill $(lsof -ti:${LOCAL_HTTP_PORT}) 2>/dev/null \
        && echo "  Killed port-forward on :${LOCAL_HTTP_PORT}"
fi

echo ""
echo "Teardown complete."
