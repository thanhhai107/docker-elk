#!/usr/bin/env bash
# Deploys ELSER v2 onto the Elasticsearch cluster and binds it to an inference
# endpoint. Idempotent: safe to re-run.
#
# Steps performed:
#   1. Verify cluster reachable and at least one node has the "ml" role.
#   2. Start a trial license if cluster is on Basic (ML requires Platinum/trial).
#   3. Register/download the ELSER model.
#   4. Wait for the model to fully download.
#   5. Start a model deployment with id ${DEPLOYMENT_ID}.
#   6. Create an inference endpoint ${INFERENCE_ID} that reuses the deployment.
#   7. Smoke-test the inference endpoint.
#   8. Write ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID} into ${ENV_FILE}.
#
# Run on the master VM after the cluster is up. Override defaults via env vars:
#   ES_URL, INFERENCE_ID, DEPLOYMENT_ID, MODEL_ID, NUMBER_OF_ALLOCATIONS,
#   NUM_THREADS, DOWNLOAD_TIMEOUT_SECONDS, DEPLOY_TIMEOUT_SECONDS, ENV_FILE.
set -euo pipefail

ES_URL="${ES_URL:-http://localhost:9200}"
INFERENCE_ID="${INFERENCE_ID:-my-elser-endpoint}"
DEPLOYMENT_ID="${DEPLOYMENT_ID:-my-elser}"
MODEL_ID="${MODEL_ID:-.elser_model_2_linux-x86_64}"
NUMBER_OF_ALLOCATIONS="${NUMBER_OF_ALLOCATIONS:-1}"
NUM_THREADS="${NUM_THREADS:-1}"
DOWNLOAD_TIMEOUT_SECONDS="${DOWNLOAD_TIMEOUT_SECONDS:-600}"
DEPLOY_TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-600}"
ENV_FILE="${ENV_FILE:-/opt/nexus/docker-elk/.env}"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "ERROR: required command not found: $1"
    exit 1
  fi
}

es_get() {
  curl -sS "$@"
}

require_command curl
require_command jq

log "Checking cluster reachable at ${ES_URL}"
if ! es_get -fsS "${ES_URL}/_cluster/health" >/dev/null; then
  log "ERROR: cannot reach Elasticsearch at ${ES_URL}"
  exit 1
fi

ML_NODES=$(es_get -fsS "${ES_URL}/_cat/nodes?h=name,node.role&format=json" \
  | jq -r '[.[] | select((.["node.role"] // .node_role) | contains("l"))] | length')
if [ "${ML_NODES}" = "0" ]; then
  log "ERROR: no Elasticsearch nodes have the 'ml' role."
  log "Add 'ml' to NEXUS_NODE_ROLES on at least one node, then recreate it."
  exit 1
fi
log "Found ${ML_NODES} ML-capable node(s)"

# 1. License: ensure ML feature is enabled (trial or platinum).
LICENSE_TYPE=$(es_get -fsS "${ES_URL}/_license" | jq -r '.license.type // "none"')
LICENSE_STATUS=$(es_get -fsS "${ES_URL}/_license" | jq -r '.license.status // "none"')
log "Current license: type=${LICENSE_TYPE}, status=${LICENSE_STATUS}"
if [ "${LICENSE_TYPE}" = "basic" ] || [ "${LICENSE_STATUS}" != "active" ]; then
  log "Starting trial license (30 days, ML enabled)"
  TRIAL_RESPONSE=$(curl -sS -X POST "${ES_URL}/_license/start_trial?acknowledge=true")
  log "Trial response: ${TRIAL_RESPONSE}"
  if printf '%s' "${TRIAL_RESPONSE}" | jq -e '.trial_was_started == true' >/dev/null 2>&1; then
    log "Trial license started"
  elif printf '%s' "${TRIAL_RESPONSE}" | jq -e '.error_message // empty' >/dev/null 2>&1; then
    REASON=$(printf '%s' "${TRIAL_RESPONSE}" | jq -r '.error_message')
    log "WARNING: could not start trial: ${REASON}"
    log "Continuing in case ML is already enabled via another license."
  fi
fi

# 2. Register/download the model. PUT is idempotent: if already registered, returns config.
log "Registering trained model ${MODEL_ID}"
MODEL_RESPONSE_FILE=$(mktemp)
trap 'rm -f "${MODEL_RESPONSE_FILE}"' EXIT
MODEL_HTTP=$(curl -sS -o "${MODEL_RESPONSE_FILE}" -w '%{http_code}' \
  -X PUT "${ES_URL}/_ml/trained_models/${MODEL_ID}?wait_for_completion=true" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"field_names":["text_field"]}}')
if [ "${MODEL_HTTP}" = "200" ] || [ "${MODEL_HTTP}" = "201" ]; then
  log "Model registered (HTTP ${MODEL_HTTP})"
elif jq -e '(.error.type // "") == "resource_already_exists_exception"' "${MODEL_RESPONSE_FILE}" >/dev/null 2>&1; then
  log "Model already registered, continuing"
else
  log "ERROR: PUT trained_models returned HTTP ${MODEL_HTTP}"
  cat "${MODEL_RESPONSE_FILE}"
  exit 1
fi

# 3. Wait for model to be fully defined (downloaded).
log "Waiting up to ${DOWNLOAD_TIMEOUT_SECONDS}s for model definition to fully download"
DEADLINE=$(( $(date +%s) + DOWNLOAD_TIMEOUT_SECONDS ))
while :; do
  DEFINED=$(es_get -fsS "${ES_URL}/_ml/trained_models/${MODEL_ID}?include=definition_status" \
    | jq -r '.trained_model_configs[0].fully_defined // false')
  if [ "${DEFINED}" = "true" ]; then
    log "Model fully defined"
    break
  fi
  if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
    log "ERROR: timed out waiting for model download. Last fully_defined=${DEFINED}"
    exit 1
  fi
  log "Model fully_defined=${DEFINED}, waiting..."
  sleep 10
done

# 4. Start deployment with DEPLOYMENT_ID. wait_for=started blocks until ready or timeout.
deployment_state() {
  es_get -fsS "${ES_URL}/_ml/trained_models/_stats" \
    | jq -r --arg dep "${DEPLOYMENT_ID}" \
      '[.trained_model_stats[] | select(.deployment_stats.deployment_id == $dep)] | first | .deployment_stats.state // "none"'
}

CURRENT_STATE=$(deployment_state)
if [ "${CURRENT_STATE}" = "started" ]; then
  log "Deployment ${DEPLOYMENT_ID} already started, skipping start"
else
  log "Starting deployment ${DEPLOYMENT_ID} (current state: ${CURRENT_STATE})"
  DEPLOY_RESPONSE_FILE=$(mktemp)
  trap 'rm -f "${MODEL_RESPONSE_FILE}" "${DEPLOY_RESPONSE_FILE}"' EXIT
  DEPLOY_HTTP=$(curl -sS -o "${DEPLOY_RESPONSE_FILE}" -w '%{http_code}' \
    -X POST "${ES_URL}/_ml/trained_models/${MODEL_ID}/deployment/_start?wait_for=started&deployment_id=${DEPLOYMENT_ID}&number_of_allocations=${NUMBER_OF_ALLOCATIONS}&threads_per_allocation=${NUM_THREADS}")
  if [ "${DEPLOY_HTTP}" = "200" ]; then
    log "Deployment started (HTTP ${DEPLOY_HTTP})"
  elif jq -e '(.error.type // "") == "status_exception" and ((.error.reason // "") | contains("already exists"))' "${DEPLOY_RESPONSE_FILE}" >/dev/null 2>&1; then
    log "Deployment already exists, continuing"
  else
    log "ERROR: deployment _start returned HTTP ${DEPLOY_HTTP}"
    cat "${DEPLOY_RESPONSE_FILE}"
    exit 1
  fi
fi

# Poll until state=started in case wait_for did not catch up.
log "Waiting up to ${DEPLOY_TIMEOUT_SECONDS}s for deployment state=started"
DEADLINE=$(( $(date +%s) + DEPLOY_TIMEOUT_SECONDS ))
while :; do
  STATE=$(deployment_state)
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

# 5. Create or refresh inference endpoint pointing to the deployment.
ENDPOINT_RESPONSE_FILE=$(mktemp)
trap 'rm -f "${MODEL_RESPONSE_FILE}" "${DEPLOY_RESPONSE_FILE:-}" "${ENDPOINT_RESPONSE_FILE}"' EXIT
log "Ensuring inference endpoint ${INFERENCE_ID} bound to deployment ${DEPLOYMENT_ID}"
ENDPOINT_PAYLOAD=$(cat <<JSON
{
  "service": "elasticsearch",
  "service_settings": {
    "num_threads": ${NUM_THREADS},
    "model_id": "${MODEL_ID}",
    "deployment_id": "${DEPLOYMENT_ID}"
  }
}
JSON
)
ENDPOINT_HTTP=$(curl -sS -o "${ENDPOINT_RESPONSE_FILE}" -w '%{http_code}' \
  -X PUT "${ES_URL}/_inference/sparse_embedding/${INFERENCE_ID}" \
  -H 'Content-Type: application/json' \
  -d "${ENDPOINT_PAYLOAD}")
if [ "${ENDPOINT_HTTP}" = "200" ]; then
  log "Inference endpoint created/updated"
elif jq -e '(.error.type // "") == "resource_already_exists_exception" or ((.error.reason // "") | contains("already exists"))' "${ENDPOINT_RESPONSE_FILE}" >/dev/null 2>&1; then
  log "Inference endpoint already exists, continuing"
else
  log "ERROR: PUT _inference returned HTTP ${ENDPOINT_HTTP}"
  cat "${ENDPOINT_RESPONSE_FILE}"
  exit 1
fi

# 6. Smoke test the endpoint.
log "Smoke-testing inference"
SMOKE=$(curl -fsS -X POST "${ES_URL}/_inference/sparse_embedding/${INFERENCE_ID}" \
  -H 'Content-Type: application/json' \
  -d '{"input":"wireless noise cancelling headphones"}')
TOKEN_COUNT=$(printf '%s' "${SMOKE}" | jq '[.sparse_embedding[0].embedding | to_entries] | length // 0')
if [ "${TOKEN_COUNT}" -gt 0 ]; then
  log "Smoke test OK (${TOKEN_COUNT} tokens returned)"
else
  log "WARNING: smoke test returned no tokens. Response:"
  printf '%s\n' "${SMOKE}"
fi

# 7. Update .env so the backend uses this inference endpoint id.
if [ -f "${ENV_FILE}" ]; then
  if grep -q '^ELASTIC_SEMANTIC_INFERENCE_ID=' "${ENV_FILE}"; then
    if ! grep -q "^ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}$" "${ENV_FILE}"; then
      log "Updating ELASTIC_SEMANTIC_INFERENCE_ID in ${ENV_FILE}"
      sudo sed -i "s|^ELASTIC_SEMANTIC_INFERENCE_ID=.*|ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}|" "${ENV_FILE}"
    else
      log "${ENV_FILE} already set to ELASTIC_SEMANTIC_INFERENCE_ID=${INFERENCE_ID}"
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