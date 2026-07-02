#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_OPENCODE:-${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}}"

if [ "$REQUIRE_REAL" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ "${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}" != "https://openrouter.ai/api/v1" ]; then
  echo "OPENROUTER_BASE_URL must be unset or exactly https://openrouter.ai/api/v1" >&2
  exit 2
fi

if [ "${AI_REVIEW_MODEL:-}" != "google/gemini-3.5-flash" ]; then
  echo "opencode model must be google/gemini-3.5-flash" >&2
  exit 2
fi

if ! command -v opencode >/dev/null 2>&1; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "opencode CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
    exit 127
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
fi

if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
  echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi

TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
REPO_SNAPSHOT_DIR="$AI_REVIEW_INPUT_DIR/repo_snapshot"
OPENCODE_REVIEW_ROOT="$TMP_DIR/opencode-review-root.$$"
OPENCODE_HOME_DIR="$TMP_DIR/opencode-home"
OPENCODE_CONFIG_HOME="$TMP_DIR/opencode-config-home"
OPENCODE_DATA_HOME="$TMP_DIR/opencode-data-home"
OPENCODE_CONFIG_DIRECTORY="$TMP_DIR/opencode-config-dir"

if [ ! -d "$REPO_SNAPSHOT_DIR" ]; then
  echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi

mkdir -p \
  "$TMP_DIR" \
  "$OPENCODE_REVIEW_ROOT" \
  "$OPENCODE_HOME_DIR" \
  "$OPENCODE_CONFIG_HOME" \
  "$OPENCODE_DATA_HOME" \
  "$OPENCODE_CONFIG_DIRECTORY"

cp -R "$REPO_SNAPSHOT_DIR"/. "$OPENCODE_REVIEW_ROOT"/
rm -f \
  "$OPENCODE_REVIEW_ROOT/opencode.json" \
  "$OPENCODE_REVIEW_ROOT/opencode.jsonc" \
  "$OPENCODE_REVIEW_ROOT/tui.json"
find "$OPENCODE_REVIEW_ROOT" -name .opencode -prune -exec rm -rf {} +

OPENCODE_CONFIG_JSON='{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openrouter": {
      "options": {
        "apiKey": "{env:OPENROUTER_API_KEY}",
        "baseURL": "https://openrouter.ai/api/v1"
      },
      "models": {
        "google/gemini-3.5-flash": {}
      }
    }
  },
  "enabled_providers": ["openrouter"],
  "agent": {
    "ai-reviewer": {
      "description": "Read-only AI code reviewer",
      "model": "openrouter/google/gemini-3.5-flash",
      "permission": {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "bash": "deny",
        "edit": "deny",
        "write": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "task": "deny",
        "skill": "deny"
      }
    }
  },
  "permission": {
    "*": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "bash": "deny",
    "edit": "deny",
    "write": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "task": "deny",
    "skill": "deny"
  }
}'

env -i \
  PATH="${PATH:-/usr/bin:/bin}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  HOME="$OPENCODE_HOME_DIR" \
  XDG_CONFIG_HOME="$OPENCODE_CONFIG_HOME" \
  XDG_DATA_HOME="$OPENCODE_DATA_HOME" \
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  OPENCODE_DISABLE_AUTOUPDATE=1 \
  OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
  OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
  OPENCODE_DISABLE_CLAUDE_CODE=1 \
  OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1 \
  OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 \
  OPENCODE_DISABLE_MODELS_FETCH=1 \
  OPENCODE_CONFIG_DIR="$OPENCODE_CONFIG_DIRECTORY" \
  OPENCODE_CONFIG_CONTENT="$OPENCODE_CONFIG_JSON" \
  opencode --pure run \
  --model "openrouter/$AI_REVIEW_MODEL" \
  --agent ai-reviewer \
  --format json \
  --dir "$OPENCODE_REVIEW_ROOT" \
  < "$AI_REVIEW_RENDERED_PROMPT"
