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

# Model is supplied via AI_REVIEW_MODEL (config default or AI_REVIEW_CODEX_MODEL
# override) and is not pinned here; the OpenRouter endpoint above remains fixed.
if [ -z "${AI_REVIEW_MODEL:-}" ]; then
  echo "AI_REVIEW_MODEL is required for the $AI_REVIEW_REVIEWER reviewer" >&2
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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AI_REVIEW_ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
REPO_SNAPSHOT_DIR="$AI_REVIEW_INPUT_DIR/repo_snapshot"
OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/raw_finding_batch.schema.json"
if [ "${AI_REVIEW_STAGE:-}" = "critique" ]; then
  OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/critique_batch.schema.json"
fi

mkdir -p "$TMP_DIR"
# Absolute paths so codex's own working root (--cd, below) never changes where
# we read/write these files.
TMP_DIR="$(cd "$TMP_DIR" && pwd)"
RAW_OUT="$TMP_DIR/${AI_REVIEW_REVIEWER}-${AI_REVIEW_STAGE}.raw.json"
CODEX_HOME_DIR="$TMP_DIR/codex-home"
CODEX_REVIEW_ROOT="$TMP_DIR/codex-review-root.$$"
mkdir -p "$CODEX_HOME_DIR" "$CODEX_REVIEW_ROOT"

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  # Explore the pinned MR snapshot, not the ambient CI checkout (which may be
  # absent under GIT_STRATEGY: none, or point at a different ref than the reviewed
  # diff). Copy into a clean root and drop codex-specific config the MR could use
  # to steer the reviewer; CODEX_HOME is already redirected, but project-level
  # AGENTS.md and .codex are read from the working tree.
  if [ ! -d "$REPO_SNAPSHOT_DIR" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  cp -R "$REPO_SNAPSHOT_DIR"/. "$CODEX_REVIEW_ROOT"/
  # AGENTS.md is resolved hierarchically, so strip it at every level, not just the
  # root, or a nested copy could still steer the reviewer. Match symlinks too, or a
  # symlinked AGENTS.md -> elsewhere would survive and still be followed.
  find "$CODEX_REVIEW_ROOT" -name AGENTS.md \( -type f -o -type l \) -delete
  find "$CODEX_REVIEW_ROOT" -name .codex -prune -exec rm -rf {} +
else
  # critique (and respond) reason only over the pooled findings in the prompt
  # (critique.md: "grounded only in the finding data, rules, and manifest"), so
  # leave the working root empty. codex still runs --sandbox read-only but has
  # nothing to explore — the same net effect as claude's tools-off critique.
  :
fi

# Codex's configured gpt-5.4-mini route accepts low|medium|high|xhigh for
# model_reasoning_effort. Keep Claude-only max at the provider default rather
# than silently coercing it to a lower supported value.
case "${AI_REVIEW_EFFORT:-}" in
  low|medium|high|xhigh) CODEX_REASONING_EFFORT="$AI_REVIEW_EFFORT" ;;
  *) CODEX_REASONING_EFFORT="" ;;
esac

set -- \
  codex exec \
  --cd "$CODEX_REVIEW_ROOT" \
  --ephemeral \
  --skip-git-repo-check \
  --ignore-user-config \
  --ignore-rules \
  --sandbox read-only \
  --model "$AI_REVIEW_MODEL" \
  --config 'model_provider="openrouter"' \
  --config 'model_providers.openrouter.name="OpenRouter"' \
  --config "model_providers.openrouter.base_url=\"$BASE_URL\"" \
  --config 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"'
if [ -n "$CODEX_REASONING_EFFORT" ]; then
  set -- "$@" --config "model_reasoning_effort=\"$CODEX_REASONING_EFFORT\""
fi
set -- "$@" --output-schema "$OUTPUT_SCHEMA" -o "$RAW_OUT" -

env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  CODEX_HOME="$CODEX_HOME_DIR" \
  "$@" < "$AI_REVIEW_RENDERED_PROMPT" >/dev/null

cat "$RAW_OUT"
