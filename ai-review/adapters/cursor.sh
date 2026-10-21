#!/bin/sh
set -eu

. "${0%/*}/common.sh"
require_model
require_cli cursor-agent
if [ -z "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
  exit 2
fi

cursor-agent "$@" < "$AI_REVIEW_RENDERED_PROMPT"
