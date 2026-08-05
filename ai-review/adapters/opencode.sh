#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_OPENCODE:-${AI_REVIEW_REQUIRE_REAL_OPENROUTER:-}}"

run_mock() {
  if [ "${AI_REVIEW_ALLOW_LOCAL_MOCK:-}" != "true" ]; then
    echo "mock reviewer fallback requires AI_REVIEW_ALLOW_LOCAL_MOCK=true" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
}

if [ "$REQUIRE_REAL" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
  run_mock
fi

if [ "${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}" != "https://openrouter.ai/api/v1" ]; then
  echo "OPENROUTER_BASE_URL must be unset or exactly https://openrouter.ai/api/v1" >&2
  exit 2
fi

# Model is supplied via AI_REVIEW_MODEL (config default or AI_REVIEW_OPENCODE_MODEL
# override) and is not pinned here; the OpenRouter endpoint above remains fixed.
if [ -z "${AI_REVIEW_MODEL:-}" ]; then
  echo "AI_REVIEW_MODEL is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi

if ! command -v opencode >/dev/null 2>&1; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "opencode CLI is required for the $AI_REVIEW_REVIEWER reviewer but was not found" >&2
    exit 127
  fi
  run_mock
fi

# Resolve a pinned CLI for forwarding into the fixed environment below, trusted
# location first. /usr/local/bin is where the image installs the pinned CLIs, so it
# must win over anything earlier on the runner's ambient PATH: forwarding an
# ambient-resolved path would let a preceding `opencode` substitute itself for the
# pinned one, which is exactly the substitution the fixed trusted PATH exists to
# prevent. Ambient resolution remains only as a fallback for checkouts and dev
# machines where the trusted location holds no such binary at all.
resolve_trusted() {
  if [ -x "/usr/local/bin/$1" ]; then
    echo "/usr/local/bin/$1"
    return 0
  fi
  command -v "$1" 2>/dev/null
}

# The fixed trusted PATH below governs what opencode itself finds — notably
# which("rg") — so it must not carry an injected binary directory, but the opencode
# executable still has to be reachable.
OPENCODE_BIN="$(resolve_trusted opencode)"

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  if [ "$REQUIRE_REAL" = "1" ]; then
    echo "OPENROUTER_API_KEY is required for the $AI_REVIEW_REVIEWER reviewer but was not set" >&2
    exit 2
  fi
  run_mock
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

mkdir -p \
  "$TMP_DIR" \
  "$OPENCODE_REVIEW_ROOT" \
  "$OPENCODE_HOME_DIR" \
  "$OPENCODE_CONFIG_HOME" \
  "$OPENCODE_DATA_HOME" \
  "$OPENCODE_CONFIG_DIRECTORY"

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  # Explore a clean copy of the pinned MR snapshot. Strip project-level config the
  # MR could use to steer the reviewer: opencode's own config files, its .opencode
  # dirs, and AGENTS.md (opencode reads AGENTS.md as agent instructions, so it
  # must be removed too — matching the codex/claude adapters). Match symlinks as
  # well as regular files, at every level.
  if [ ! -d "$REPO_SNAPSHOT_DIR" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  cp -R "$REPO_SNAPSHOT_DIR"/. "$OPENCODE_REVIEW_ROOT"/
  find "$OPENCODE_REVIEW_ROOT" \
    \( -name opencode.json -o -name opencode.jsonc -o -name tui.json -o -name AGENTS.md \) \
    \( -type f -o -type l \) -delete
  find "$OPENCODE_REVIEW_ROOT" -name .opencode -prune -exec rm -rf {} +
else
  # critique reasons only over the pooled findings in the prompt
  # (critique.md: "grounded only in the finding data, rules, and manifest"), so
  # leave the working root empty — read/glob/grep stay allowed but have nothing to
  # explore, the same net effect as claude's tools-off critique.
  :
fi

# Map supported reviewer effort values onto OpenCode's reasoningEffort unchanged.
# Provider/model-specific rejection remains visible rather than silently falling
# back to a different effort or the provider default.
case "${AI_REVIEW_EFFORT:-}" in
  low|medium|high|xhigh|max) OPENCODE_REASONING_EFFORT="$AI_REVIEW_EFFORT" ;;
  *) OPENCODE_REASONING_EFFORT="" ;;
esac

OPENCODE_AGENT_EXTRA_JSON=""
if [ -n "$OPENCODE_REASONING_EFFORT" ]; then
  OPENCODE_AGENT_EXTRA_JSON="${OPENCODE_AGENT_EXTRA_JSON}      \"reasoningEffort\": \"$OPENCODE_REASONING_EFFORT\",
"
fi

# Unquoted heredoc so $AI_REVIEW_MODEL and guarded optional fragments expand;
# \$schema stays literal and the {env:OPENROUTER_API_KEY} template (no leading
# $) is passed through untouched.
#
# external_directory must be denied explicitly. It is a permission key of its own,
# so the "*": "deny" tool wildcard does not cover it, and OpenCode's default is
# {"*": "ask"} — verified with `opencode --pure debug agent ai-reviewer` against
# this exact config. Without the explicit rule, read/grep on an absolute path
# outside the review root raises an approval request that nothing in a headless
# reviewer can answer, and the sanitized snapshot boundary stops being the
# reviewer's actual reach.
OPENCODE_CONFIG_JSON=$(cat <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "lsp": false,
  "formatter": false,
  "provider": {
    "openrouter": {
      "options": {
        "apiKey": "{env:OPENROUTER_API_KEY}",
        "baseURL": "https://openrouter.ai/api/v1"
      },
      "models": {
        "$AI_REVIEW_MODEL": {}
      }
    }
  },
  "enabled_providers": ["openrouter"],
  "agent": {
    "ai-reviewer": {
      "description": "Read-only AI code reviewer",
      "model": "openrouter/$AI_REVIEW_MODEL",
$OPENCODE_AGENT_EXTRA_JSON      "permission": {
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
        "skill": "deny",
        "external_directory": {"*": "deny"}
      },
      "// tools": "Trim schemas for tools already denied by permission; permission remains enforcement.",
      "tools": {
        "bash": false,
        "edit": false,
        "write": false,
        "patch": false,
        "webfetch": false,
        "websearch": false,
        "task": false,
        "todowrite": false,
        "todoread": false,
        "skill": false
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
    "skill": "deny",
    "external_directory": {"*": "deny"}
  }
}
EOF
)

# OpenCode's `run --format json` only selects raw event output; it does not enforce
# a response schema. The internal client below uses the pinned server API so the
# stage schema reaches OpenCode's required StructuredOutput tool. It keeps the
# title static and data-free to avoid the separate title-inference request.
# The interpreter is resolved like OPENCODE_BIN, and for both reasons: the fixed
# trusted PATH below is deliberate, but a bare `python3` (a venv, a Homebrew
# install, anything outside /usr/local/bin:/usr/bin:/bin) would not be reachable
# from it — and an ambient-resolved python3 could itself be shadowed. An explicitly
# set $PYTHON is honored verbatim, because that is a caller's deliberate choice, not
# a lookup. Fall back to the unresolved name so the failure still names what was
# missing.
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
else
  PYTHON_BIN="$(resolve_trusted python3 || printf 'python3')"
fi
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHONPATH_VALUE="${PYTHONPATH:-$SCRIPT_DIR/../src}"
# Fixed trusted PATH, not the runner's ambient one: /usr/local/bin must win so
# OpenCode's which("rg") resolves the pinned image rg and never falls through to
# a download or to some earlier rg on the ambient PATH. This is the exact PATH the
# reviewer.Dockerfile final guard proves resolution on.
env -i \
  PATH="/usr/local/bin:/usr/bin:/bin" \
  OPENCODE_BIN="$OPENCODE_BIN" \
  PYTHON="$PYTHON_BIN" \
  PYTHONPATH="$PYTHONPATH_VALUE" \
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
  AI_REVIEW_INPUT_DIR="$AI_REVIEW_INPUT_DIR" \
  AI_REVIEW_OUTPUT_DIR="$AI_REVIEW_OUTPUT_DIR" \
  AI_REVIEW_MODEL="$AI_REVIEW_MODEL" \
  AI_REVIEW_STAGE="${AI_REVIEW_STAGE:-}" \
  AI_REVIEW_RENDERED_PROMPT="$AI_REVIEW_RENDERED_PROMPT" \
  AI_REVIEW_OPENCODE_ROOT="$OPENCODE_REVIEW_ROOT" \
  "$PYTHON_BIN" -m ai_review.opencode_client
