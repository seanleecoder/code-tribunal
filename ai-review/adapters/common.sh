# shellcheck shell=sh
# Prerequisite checks for the command-only shell adapters.

# Fail with exit 127 when the seat's CLI is not on PATH.
#   require_cli <command>
require_cli() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
    exit 127
  fi
}

# Reject an empty AI_REVIEW_MODEL.
require_model() {
  if [ -z "${AI_REVIEW_MODEL:-}" ]; then
    echo "AI_REVIEW_MODEL is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
}
