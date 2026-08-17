#!/bin/sh
set -eu

REQUIRE_REAL="${AI_REVIEW_REQUIRE_REAL_CLAUDE:-}"
. "${0%/*}/common.sh"

mock_if_requested

if ! command -v claude >/dev/null 2>&1; then
  require_real_or_mock \
    "claude CLI is required for this AI review job but was not found" 127
fi

export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY:-}"
if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "OpenRouter review requires OPENROUTER_API_KEY or ANTHROPIC_AUTH_TOKEN" >&2
  exit 2
fi

export ANTHROPIC_MODEL="${AI_REVIEW_MODEL}"

resolve_prompt_file
resolve_output_schema

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
#
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

# --bare cannot authenticate with the OpenRouter ANTHROPIC_AUTH_TOKEN route, so
# the adapter relies on --safe-mode plus the explicit stage tool allowlist.

# Default working directory for stages that don't explore the repo.
RUN_DIR="."

if [ "${AI_REVIEW_STAGE:-}" = "review" ]; then
  # The review stage reads the wider codebase to ground findings, so root the
  # agent at a clean copy of the pinned MR snapshot. Without this, claude ran in
  # the ambient CI build dir, so paths from the diff (e.g. src/foo.py) never
  # resolved and the "explore the codebase" prompt sent the agent searching from
  # the wrong root until it hit the reviewer timeout.
  #
  # --safe-mode already ignores CLAUDE.md; stripping it, AGENTS.md and .claude
  # from the copy is defense in depth.
  resolve_tmp_dir
  CLAUDE_REVIEW_ROOT="$TMP_DIR/claude-review-root.$$"
  mkdir -p "$CLAUDE_REVIEW_ROOT"
  # Remove the snapshot copy on exit so repeated local-harness runs don't
  # accumulate review roots (in CI the whole container is ephemeral anyway).
  trap 'rm -rf "$CLAUDE_REVIEW_ROOT"' EXIT
  prepare_review_root "$CLAUDE_REVIEW_ROOT" CLAUDE.md AGENTS.md .claude/

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
# the stage-specific runner timeout as a hang-catch). Sourced from reviewers.<name>.effort in
# review.yaml (runtime override: AI_REVIEW_<REVIEWER>_EFFORT), exported by the
# runner as AI_REVIEW_EFFORT and validated there against a closed set.
if [ -n "${AI_REVIEW_EFFORT:-}" ]; then
  set -- "$@" --effort "$AI_REVIEW_EFFORT"
fi

cd "$RUN_DIR"
claude "$@" < "$PROMPT_FILE"
