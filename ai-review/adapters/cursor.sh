#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_CURSOR:-}"

if [ "$REQUIRE_REAL" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${AI_REVIEW_MODEL:-}" ]; then
  echo "AI_REVIEW_MODEL is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi

if ! command -v cursor-agent >/dev/null 2>&1; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "cursor-agent CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
    exit 127
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${CURSOR_API_KEY:-}" ]; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "CURSOR_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
  echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi
PROMPT_FILE="$(cd -- "$(dirname -- "$AI_REVIEW_RENDERED_PROMPT")" && pwd)/$(basename -- "$AI_REVIEW_RENDERED_PROMPT")"

TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
mkdir -p "$TMP_DIR"
TMP_DIR="$(cd "$TMP_DIR" && pwd)"
REPO_SNAPSHOT_DIR="$AI_REVIEW_INPUT_DIR/repo_snapshot"
CURSOR_REVIEW_ROOT="$TMP_DIR/cursor-review-root.$$"
CURSOR_HOME_DIR="$TMP_DIR/cursor-home"
mkdir -p "$CURSOR_REVIEW_ROOT" "$CURSOR_HOME_DIR/.cursor"
trap 'rm -rf "$CURSOR_REVIEW_ROOT"' EXIT

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  if [ ! -d "$REPO_SNAPSHOT_DIR" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  cp -R "$REPO_SNAPSHOT_DIR"/. "$CURSOR_REVIEW_ROOT"/
  find "$CURSOR_REVIEW_ROOT" \
    \( -name .cursorrules -o -name .cursorignore -o -name AGENTS.md -o -name CLAUDE.md \) \
    \( -type f -o -type l \) -delete
  find "$CURSOR_REVIEW_ROOT" -name .cursor -prune -exec rm -rf {} +
fi

cat > "$CURSOR_HOME_DIR/.cursor/cli-config.json" <<'JSON'
{"permissions":{"allow":["Read(**)"],"deny":["Write(**)","Shell(**)"]}}
JSON

cd "$CURSOR_REVIEW_ROOT"
# Cursor's kernel sandbox is unavailable in GitHub's job containers. Use its
# allowlist mode in the disposable HOME instead; cli-config.json still denies
# writes and shell commands, and the workspace is the sanitized snapshot.
env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  HOME="$CURSOR_HOME_DIR" \
  CURSOR_API_KEY="$CURSOR_API_KEY" \
  cursor-agent sandbox disable >/dev/null

env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  HOME="$CURSOR_HOME_DIR" \
  CURSOR_API_KEY="$CURSOR_API_KEY" \
  cursor-agent -p \
  --output-format json \
  --trust \
  --model "$AI_REVIEW_MODEL" \
  < "$PROMPT_FILE"
