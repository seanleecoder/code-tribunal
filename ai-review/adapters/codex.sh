#!/bin/sh
set -eu

# The seat's REQUIRE_REAL control name is owned by the reviewer registry
# (reviewers.py) and is the only one the runner forwards; a second name read here
# would be dead code.
REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}"
. "${0%/*}/common.sh"

mock_if_requested

# Model is supplied via AI_REVIEW_MODEL (config default or AI_REVIEW_CODEX_MODEL
# override) and is not pinned here. The OpenRouter endpoint is fixed but not
# checked here: the runner injects OPENROUTER_BASE_URL from the registry's
# endpoint_kind, so a non-canonical value cannot reach this adapter.
require_model

if ! command -v codex >/dev/null 2>&1; then
  require_real_or_mock \
    "codex CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" 127
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  require_real_or_mock \
    "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set"
fi

resolve_prompt_file
resolve_tmp_dir
resolve_output_schema

BASE_URL="$OPENROUTER_BASE_URL"
RAW_OUT="$TMP_DIR/${AI_REVIEW_REVIEWER}-${AI_REVIEW_STAGE}.raw.json"
CODEX_HOME_DIR="$TMP_DIR/codex-home"
CODEX_REVIEW_ROOT="$TMP_DIR/codex-review-root.$$"
mkdir -p "$CODEX_HOME_DIR" "$CODEX_REVIEW_ROOT"

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  # Explore the pinned MR snapshot, not the ambient CI checkout (which may be
  # absent under GIT_STRATEGY: none, or point at a different ref than the reviewed
  # diff). CODEX_HOME is already redirected; AGENTS.md and .codex are read from
  # the working tree, so they are stripped from the copy.
  prepare_review_root "$CODEX_REVIEW_ROOT" AGENTS.md .codex/
fi
# critique reasons only over the pooled findings in the prompt (critique.md:
# "grounded only in the finding data, rules, and manifest"), so the working root
# stays empty. codex still runs --sandbox read-only but has nothing to explore —
# the same net effect as claude's tools-off critique.

# Pass every supported reviewer effort value through to Codex unchanged as
# model_reasoning_effort. The closed config enum and this case guard keep the
# value safe for the --config argument.
case "${AI_REVIEW_EFFORT:-}" in
  low|medium|high|xhigh|max) CODEX_REASONING_EFFORT="$AI_REVIEW_EFFORT" ;;
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
  "$@" < "$PROMPT_FILE" >/dev/null

cat "$RAW_OUT"
