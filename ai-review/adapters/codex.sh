#!/bin/sh
set -eu

if [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

echo "codex adapter provider mode is a Phase 2 implementation item" >&2
exit 1
