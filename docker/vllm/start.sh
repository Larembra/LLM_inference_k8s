#!/bin/sh
# Параметры приходят из ConfigMap (envFrom), токен API — из Secret через VLLM_API_KEY (не через argv).
set -eu

printf 'MODEL_PATH=%s\nSERVED_MODEL_NAME=%s\n' "$MODEL_PATH" "$SERVED_MODEL_NAME"
printf 'ARGS:'
printf ' <%s>' "$@"
printf '\n'

: "${VLLM_API_KEY:?VLLM_API_KEY is required: refusing to start an unauthenticated API}"
: "${MODEL_PATH:?}" "${SERVED_MODEL_NAME:?}" "${MAX_MODEL_LEN:?}" "${MAX_NUM_SEQS:?}" "${MAX_NUM_BATCHED_TOKENS:?}"

# shellcheck disable=SC2086  # LLM_EXTRA_ARGS намеренно разбивается по пробелам
exec python -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_PATH}" \
  --served-model-name "${SERVED_MODEL_NAME}" \
  --host 0.0.0.0 --port 8000 \
  --dtype "${LLM_DTYPE:-auto}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --max-num-seqs "${MAX_NUM_SEQS}" \
  --max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS}" \
  --middleware request_audit.RequestAuditMiddleware \
  --disable-uvicorn-access-log \
  --uvicorn-log-level "${UVICORN_LOG_LEVEL:-warning}" \
  ${LLM_EXTRA_ARGS:-}
