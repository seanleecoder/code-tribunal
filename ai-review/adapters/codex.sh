#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_CODEX:-${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}}"

if [ "$REQUIRE_REAL" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ "${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}" != "https://openrouter.ai/api/v1" ]; then
  echo "OPENROUTER_BASE_URL must be unset or exactly https://openrouter.ai/api/v1" >&2
  exit 2
fi

if [ "${AI_REVIEW_MODEL:-}" != "openai/gpt-5.4-mini" ]; then
  echo "codex model must be openai/gpt-5.4-mini" >&2
  exit 2
fi

if ! command -v codex >/dev/null 2>&1; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "codex CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
    exit 127
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
  echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi

BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
RAW_OUT="$TMP_DIR/${AI_REVIEW_REVIEWER}-${AI_REVIEW_STAGE}.raw.json"
CODEX_HOME_DIR="$TMP_DIR/codex-home"
mkdir -p "$TMP_DIR" "$CODEX_HOME_DIR"

env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  CODEX_HOME="$CODEX_HOME_DIR" \
  codex exec \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --sandbox read-only \
  --model "$AI_REVIEW_MODEL" \
  --config 'model_provider="openrouter"' \
  --config 'model_providers.openrouter.name="OpenRouter"' \
  --config "model_providers.openrouter.base_url=\"$BASE_URL\"" \
  --config 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"' \
  --output-schema ai-review/schemas/raw_finding_batch.schema.json \
  -o "$RAW_OUT" \
  - < "$AI_REVIEW_RENDERED_PROMPT" >/dev/null

cat "$RAW_OUT"
