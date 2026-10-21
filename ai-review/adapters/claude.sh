#!/bin/sh
set -eu

. "${0%/*}/common.sh"
require_model
require_cli claude
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
  exit 2
fi

claude "$@" < "$AI_REVIEW_RENDERED_PROMPT"
