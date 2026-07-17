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

if [ "${ANTHROPIC_BASE_URL:-}" = "https://openrouter.ai/api" ]; then
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then
    export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
  fi
  export ANTHROPIC_API_KEY=""
  if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "OpenRouter review requires OPENROUTER_API_KEY or ANTHROPIC_AUTH_TOKEN" >&2
    exit 2
  fi
fi

export ANTHROPIC_MODEL="${AI_REVIEW_MODEL}"

if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
  echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
  exit 2
fi
# Resolve to an absolute path so the stdin redirect still points at the prompt
# after we cd into the review root below. `--` guards against a path that begins
# with a hyphen being parsed as an option by cd/dirname/basename.
PROMPT_FILE="$(cd -- "$(dirname -- "$AI_REVIEW_RENDERED_PROMPT")" && pwd)/$(basename -- "$AI_REVIEW_RENDERED_PROMPT")"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AI_REVIEW_ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ask claude for schema-conforming structured output, mirroring codex's
# --output-schema. The terminal stream-json result event then carries the
# findings in a `structured_output` field. This is best-effort steering, not
# enforcement — the runner still falls back to parsing the result text when
# structured_output is absent, and real conformance is enforced downstream by
# finalize_finding_batch + JSON-schema validation.
# Note: the docs pair --json-schema with --output-format json; its interaction
# with stream-json is verified per pinned CLI (full review-stage flag set) by
# the image-build smoke test in images/reviewer.Dockerfile. If
# structured_output is absent the runner falls back to parsing the result text
# (the prompt still demands final JSON) and says so in the job log, so the
# worst case equals the pre-json-schema pipeline — visibly, not silently.
OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/raw_finding_batch.schema.json"
if [ "${AI_REVIEW_STAGE:-}" = "critique" ]; then
  OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/critique_batch.schema.json"
fi

# The CLI rejects schemas that declare the 2020-12 draft ("--json-schema is
# not a valid JSON Schema: no schema with key or ref https://json-schema.org/
# draft/2020-12/schema"), so strip the $schema key when passing the shared
# schema files; $id, $ref, $defs, pattern and const are accepted as-is
# (verified against the pinned CLI, and re-verified per pinned CLI by the
# image-build smoke test).
JSON_SCHEMA_VALUE="$("${PYTHON:-python3}" -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    schema = json.load(handle)
schema.pop("$schema", None)
print(json.dumps(schema))
' "$OUTPUT_SCHEMA")"

set -- -p \
  --safe-mode \
  --model "${AI_REVIEW_MODEL}" \
  --no-session-persistence \
  --output-format stream-json \
  --verbose \
  --json-schema "$JSON_SCHEMA_VALUE"

# --bare skips startup auto-discovery (hooks, skills, plugins, MCP, auto
# memory, CLAUDE.md) on top of --safe-mode, but it restricts Anthropic auth to
# strictly ANTHROPIC_API_KEY — so skip it on the OpenRouter route, which
# authenticates via ANTHROPIC_AUTH_TOKEN (mapped above).
if [ "${ANTHROPIC_BASE_URL:-}" != "https://openrouter.ai/api" ]; then
  set -- "$@" --bare
fi

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
  # critique reasons only over the finding/manifest payload already
  # in the prompt — critique.md says to stay "grounded only in the finding data,
  # rules, and manifest". Disable tools so claude answers in one shot instead of
  # agentically exploring the snapshot (with no turn cap) and blowing the
  # timeout the way the review stage did before it was rooted.
  set -- "$@" --tools ""
fi

# Effort modulates how much reasoning/exploration the model volunteers — it is
# NOT a turn cap; the agentic loop still runs to completion (bounded only by
# timeout_seconds as a hang-catch). Sourced from reviewers.<name>.effort in
# review.yaml (runtime override: AI_REVIEW_<REVIEWER>_EFFORT), exported by the
# runner as AI_REVIEW_EFFORT and validated there against a closed set.
if [ -n "${AI_REVIEW_EFFORT:-}" ]; then
  set -- "$@" --effort "$AI_REVIEW_EFFORT"
fi

cd "$RUN_DIR"
claude "$@" < "$PROMPT_FILE"
