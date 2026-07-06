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

if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
  echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi
# Resolve to an absolute path so the stdin redirect still points at the prompt
# after we cd into the review root below. `--` guards against a path that begins
# with a hyphen being parsed as an option by cd/dirname/basename.
PROMPT_FILE="$(cd -- "$(dirname -- "$AI_REVIEW_RENDERED_PROMPT")" && pwd)/$(basename -- "$AI_REVIEW_RENDERED_PROMPT")"

set -- -p \
  --safe-mode \
  --model "${AI_REVIEW_MODEL}" \
  --no-session-persistence \
  --output-format stream-json \
  --verbose

# Default working directory for stages that don't explore the repo.
RUN_DIR="."

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  # The review stage reads the wider codebase to ground findings, so root the
  # agent at a clean copy of the pinned MR snapshot with the reviewed files at
  # the working-tree root — the same way the codex (--cd) and opencode (--dir)
  # adapters do. Without this, claude ran in the ambient CI build dir, so paths
  # from the diff (e.g. src/foo.py) never resolved and the "explore the
  # codebase" prompt sent the agent searching from the wrong root until it hit
  # the reviewer timeout.
  REPO_SNAPSHOT_DIR="$AI_REVIEW_INPUT_DIR/repo_snapshot"
  if [ ! -d "$REPO_SNAPSHOT_DIR" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi

  TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
  mkdir -p "$TMP_DIR"
  # Absolute so the review root path survives the cd below.
  TMP_DIR="$(cd "$TMP_DIR" && pwd)"
  CLAUDE_REVIEW_ROOT="$TMP_DIR/claude-review-root.$$"
  mkdir -p "$CLAUDE_REVIEW_ROOT"
  # Remove the snapshot copy on exit so repeated local-harness runs don't
  # accumulate review roots (in CI the whole container is ephemeral anyway).
  trap 'rm -rf "$CLAUDE_REVIEW_ROOT"' EXIT

  # Copy into a clean root and strip project-level agent config the MR could use
  # to steer the reviewer; --safe-mode already ignores CLAUDE.md, but delete it
  # (and .claude / AGENTS.md) at every level as defense in depth. Match both
  # regular files and symlinks so a symlinked CLAUDE.md/AGENTS.md -> elsewhere
  # can't survive the strip and still be followed.
  cp -R "$REPO_SNAPSHOT_DIR"/. "$CLAUDE_REVIEW_ROOT"/
  find "$CLAUDE_REVIEW_ROOT" -name CLAUDE.md \( -type f -o -type l \) -delete
  find "$CLAUDE_REVIEW_ROOT" -name AGENTS.md \( -type f -o -type l \) -delete
  find "$CLAUDE_REVIEW_ROOT" -name .claude -prune -exec rm -rf {} +

  RUN_DIR="$CLAUDE_REVIEW_ROOT"
  set -- "$@" --add-dir "$CLAUDE_REVIEW_ROOT" --tools "Read,Grep,Glob"
else
  # critique (and respond) reason only over the finding/manifest payload already
  # in the prompt — critique.md says to stay "grounded only in the finding data,
  # rules, and manifest". Disable tools so claude answers in one shot instead of
  # agentically exploring the snapshot (with no turn cap) and blowing the
  # timeout the way the review stage did before it was rooted.
  set -- "$@" --tools ""
fi

# Turn cap is opt-in. Unset by default so Claude runs its agentic loop to
# completion (bounded by the reviewer timeout), matching the codex/opencode
# adapters. A hard-coded low cap made stronger models hit error_max_turns
# before emitting findings. Set AI_REVIEW_MAX_TURNS (or the reviewer's
# max_turns in review.yaml) to re-impose one.
MAX_TURNS_VALUE="${AI_REVIEW_MAX_TURNS:-${MAX_TURNS:-}}"
if [ -n "$MAX_TURNS_VALUE" ]; then
  set -- "$@" --max-turns "$MAX_TURNS_VALUE"
fi

cd "$RUN_DIR"
claude "$@" < "$PROMPT_FILE"
