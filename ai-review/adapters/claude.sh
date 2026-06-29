#!/bin/sh
set -eu

if [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ] || ! command -v claude >/dev/null 2>&1; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

claude -p \
  --safe-mode \
  --no-session-persistence \
  --tools "Read,Grep,Glob" \
  --max-turns "${MAX_TURNS:-4}" \
  "$(cat "$AI_REVIEW_RENDERED_PROMPT")"
