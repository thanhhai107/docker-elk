#!/usr/bin/env bash
# Deploys ELSER v2 onto the Elasticsearch cluster and exposes it as the
# inference endpoint named "my-elser". Idempotent: safe to re-run.
#
# Run this on the master VM after the cluster is up and at least one node has
# the "ml" role.
set -euo pipefail

ES_URL="${ES_URL:-http://localhost:9200}"
INFERENCE_ID="${INFERENCE_ID:-my-elser}"
MODEL_ID="${MODEL_ID:-.elser_model_2_linux-x86_64}"
MIN_ALLOCATIONS="${MIN_ALLOCATIONS:-1}"
MAX_ALLOCATIONS="${MAX_ALLOCATIONS:-4}"
NUM_THREADS="${NUM_THREADS:-1}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-600}"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "ERROR: required command not found: $1"
    exit 1
  fi
}

require_command curl
require_command jq

log "Checking cluster reachable at ${ES_URL}"
if ! curl -fsS "${ES_URL}/_cluster/health" >/dev/null; then
  log "ERROR: cannot reach Elasticsearch at ${ES_URL}"
  exit 1
 fi

ML_NODES=$(curl -fsS "${ES_URL}/_cat/nodes?h=name,node.role&format=json" \
  | jq -r '[.[] | select((.["node.role"] // .node_role) | contains("l"))] | length')
if [ "${ML_NODES}" = "0" ]; then
  log "ERROR: no Elasticsearch nodes have the 'ml' role."
  log "Add 'ml' to NEXUS_NODE_ROLES on at least one node, then recreate it."
  exit 1
fi
log "Found ${ML_NODES} ML-capable node(s)"

log "Creating/refreshing inference endpoint '${INFERENCE_ID}' bound to model '${MODEL_ID}'"
PAYLOAD=$(cat <<JSON
{
  "service": "elasticsearch",
  "service_settings": {
    "adaptive_allocations": {
      "enabled": true,
      "min_number_of_allocations": ${MIN_ALLOCATIONS},
      "max_number_of_allocations": ${MAX_ALLOCATIONS}
    },
    "num_threads": ${NUM_THREADS},
    "model_id": "${MODEL_ID}"
  }
}
JSON
)

HTTP_STATUS=$(curl -sS -o /tmp/setup_elser_response.json -w '%{http_code}' \
  -X PUT "${ES_URL}/_inference/sparse_embedding/${INFERENCE_ID}" \
  -H 'Content-Type: application/json' \
  -d "${PAYLOAD}")

case "${HTTP_STATUS}" in
  2*)
    log "Inference endpoint accepted (HTTP ${HTTP_STATUS})"
    ;;
  409)
    log "Inference endpoint already exists (HTTP 409), continuing"
    ;;
  *)
    log "ERROR: PUT _inference returned HTTP ${HTTP_STATUS}"
    cat /tmp/setup_elser_response.json
    exit 1
    ;;
esac

log "Waiting for model deployment to reach 'started' (timeout ${WAIT_TIMEOUT_SECONDS}s)"
DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
while :; do
  STATE=$(curl -fsS "${ES_URL}/_ml/trained_models/${MODEL_ID}/_stats" \
    | jq -r '.trained_model_stats[0].deployment_stats.state // "unknown"')
  if [ "${STATE}" = "started" ]; then
    log "Deployment state: started"
    break
  fi
  if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
    log "ERROR: timed out waiting for deployment. Last state: ${STATE}"
    exit 1
  fi
  log "Deployment state: ${STATE}, waiting..."
  sleep 10
done

log "Smoke-testing inference"
SMOKE=$(curl -fsS -X POST "${ES_URL}/_inference/sparse_embedding/${INFERENCE_ID}" \
  -H 'Content-Type: application/json' \
  -d '{"input":"wireless noise cancelling headphones"}')
TOKEN_COUNT=$(printf '%s' "${SMOKE}" \
  | jq '[.sparse_embedding[0].embedding | to_entries] | length // 0')
if [ "${TOKEN_COUNT}" -gt 0 ]; then
  log "Smoke test OK (${TOKEN_COUNT} tokens returned)"
else
  log "WARNING: smoke test returned no tokens. Response:"
  printf '%s\n' "${SMOKE}"
fi

ENV_FILE="${ENV_FILE:-/opt/nexus/docker-elk/.env}"
if [ -f "${ENV_FILE}" ]; then
  if grep -q '^ELASTIC_SEMANTIC_INFERENCE_ID=' "${ENV_FILE}"; then
    if ! grep -q "^ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}$" "${ENV_FILE}"; then
      log "Updating ELASTIC_SEMANTIC_INFERENCE_ID in ${ENV_FILE}"
      sudo sed -i "s|^ELASTIC_SEMANTIC_INFERENCE_ID=.*|ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}|" "${ENV_FILE}"
    else
      log "${ENV_FILE} already has ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}"
    fi
  else
    log "Appending ELASTIC_SEMANTIC_INFERENCE_ID to ${ENV_FILE}"
    printf 'ELASTIC_SEMANTIC_INFERENCE_ID=%s\n' "${INFERENCE_ID}" | sudo tee -a "${ENV_FILE}" >/dev/null
  fi
else
  log "NOTE: ${ENV_FILE} not found, skipping env update"
fi

log "Done. Next steps:"
log "  - Recreate backend so it picks up ELASTIC_SEMANTIC_INFERENCE_ID:"
log "      cd /opt/nexus/docker-elk && docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d --force-recreate backend"
log "  - Re-ingest Elasticsearch (processed JSONL is reused):"
log "      docker compose exec -T backend python scripts/ingest_all.py --reset --engine elasticsearch"