#!/bin/sh
set -eu

if [ "${AI_REVIEW_REQUIRE_REAL_CLAUDE:-}" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if ! command -v claude >/dev/null 2>&1; then
  if [ "${AI_REVIEW_REQUIRE_REAL_CLAUDE:-}" = "1" ]; then
    echo "claude CLI is required for this AI review job but was not found" >&2
    exit 127
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

case "${ANTHROPIC_BASE_URL:-}" in
  *openrouter.ai*)
    if [ -n "${OPENROUTER_API_KEY:-}" ]; then
      export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
    fi
    export ANTHROPIC_API_KEY=""
    if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
      echo "OpenRouter review requires OPENROUTER_API_KEY or ANTHROPIC_AUTH_TOKEN" >&2
      exit 2
    fi
    ;;
esac

export ANTHROPIC_MODEL="${AI_REVIEW_MODEL}"

claude -p \
  --safe-mode \
  --model "${AI_REVIEW_MODEL}" \
  --no-session-persistence \
  --output-format stream-json \
  --verbose \
  --tools "Read,Grep,Glob" \
  --max-turns "${AI_REVIEW_MAX_TURNS:-${MAX_TURNS:-4}}" \
  < "$AI_REVIEW_RENDERED_PROMPT"
