#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_CURSOR:-}"
. "${0%/*}/common.sh"

mock_if_requested
require_model

if ! command -v cursor-agent >/dev/null 2>&1; then
  require_real_or_mock \
    "cursor-agent CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" 127
fi

if [ -z "${CURSOR_API_KEY:-}" ]; then
  require_real_or_mock \
    "CURSOR_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set"
fi

resolve_prompt_file
resolve_tmp_dir

CURSOR_REVIEW_ROOT="$TMP_DIR/cursor-review-root.$$"
CURSOR_HOME_DIR="$TMP_DIR/cursor-home"
mkdir -p "$CURSOR_REVIEW_ROOT" "$CURSOR_HOME_DIR/.cursor"
trap 'rm -rf "$CURSOR_REVIEW_ROOT"' EXIT

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  prepare_review_root "$CURSOR_REVIEW_ROOT" \
    .cursorrules .cursorignore AGENTS.md CLAUDE.md .cursor/
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
