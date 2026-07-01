#!/bin/sh
set -eu

if [ "${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  if [ "${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}" = "1" ]; then
    echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

exec "${PYTHON:-python3}" -m ai_review.openrouter_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
