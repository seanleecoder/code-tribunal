#!/bin/sh
set -eu

. "${0%/*}/common.sh"

require_model

if ! command -v cursor-agent >/dev/null 2>&1; then
  echo "cursor-agent CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
  exit 127
fi

if [ -z "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
  exit 2
fi

resolve_prompt_file
resolve_tmp_dir

CURSOR_REVIEW_ROOT="$TMP_DIR/cursor-review-root.$$"
CURSOR_HOME_DIR="$TMP_DIR/cursor-home"
mkdir -p "$CURSOR_REVIEW_ROOT" "$CURSOR_HOME_DIR/.cursor"
trap 'rm -rf "$CURSOR_REVIEW_ROOT"' EXIT

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  copy_review_snapshot "$CURSOR_REVIEW_ROOT"
fi

cat > "$CURSOR_HOME_DIR/.cursor/cli-config.json" <<'JSON'
{"permissions":{"allow":["Read(**)"],"deny":["Write(**)","Write(/**)","Shell(*)"]}}
JSON

cd "$CURSOR_REVIEW_ROOT"
# Cursor's kernel sandbox is unavailable in GitHub's job containers. Use its
# native read-only ask mode in the disposable HOME instead; cli-config.json
# also denies relative and absolute writes plus every shell command, and the
# workspace is the sanitized snapshot.
env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  HOME="$CURSOR_HOME_DIR" \
  CURSOR_API_KEY="$CURSOR_API_KEY" \
  cursor-agent -p \
  --output-format json \
  --trust \
  --sandbox disabled \
  --mode ask \
  --model "$AI_REVIEW_MODEL" \
  < "$PROMPT_FILE"
