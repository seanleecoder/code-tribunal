#!/bin/sh
set -eu

. "${0%/*}/common.sh"
require_model
require_cli codex
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
  exit 2
fi

# The registry selects --json. Preserve execution events as well as the answer;
# a valid final batch cannot hide an unusable repository-tool runtime. The
# runner's PYTHONPATH supplies the trusted filter, which drops repository-tool
# results the codec never reads, so tool traffic cannot reach the output limit.
exec "${PYTHON:-python3}" -P -m ai_review.codex_stream_filter codex "$@" \
  < "$AI_REVIEW_RENDERED_PROMPT"
