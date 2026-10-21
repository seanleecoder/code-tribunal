# shellcheck shell=sh
# Workspace and prompt scaffolding sourced by the shell adapters.

# `$0` is the sourcing adapter under POSIX sh, so this resolves relative to the
# adapter rather than to this file. Only shell builtins are used so missing-CLI
# checks still run under a stripped PATH.
AI_REVIEW_ROOT_DIR="$(CDPATH= cd -- "${0%/*}/.." && pwd)"

# Reject an empty AI_REVIEW_MODEL.
require_model() {
  if [ -z "${AI_REVIEW_MODEL:-}" ]; then
    echo "AI_REVIEW_MODEL is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
}

# Set PROMPT_FILE to an absolute path, so a later `cd` into the review root does
# not break the stdin redirect. `--` guards a path that begins with a hyphen.
resolve_prompt_file() {
  if [ -z "${AI_REVIEW_RENDERED_PROMPT:-}" ] || [ ! -f "$AI_REVIEW_RENDERED_PROMPT" ]; then
    echo "AI_REVIEW_RENDERED_PROMPT is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  PROMPT_FILE="$(CDPATH= cd -- "$(dirname -- "$AI_REVIEW_RENDERED_PROMPT")" && pwd)/$(basename -- "$AI_REVIEW_RENDERED_PROMPT")"
}

# Set TMP_DIR to an absolute scratch directory under the output dir.
resolve_tmp_dir() {
  TMP_DIR="${AI_REVIEW_OUTPUT_DIR:-out}/.tmp"
  mkdir -p "$TMP_DIR"
  TMP_DIR="$(CDPATH= cd -- "$TMP_DIR" && pwd)"
}

# Set OUTPUT_SCHEMA to the schema the current stage must conform to.
resolve_output_schema() {
  if [ "${AI_REVIEW_STAGE:-}" = "critique" ]; then
    OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/critique_batch.schema.json"
  else
    OUTPUT_SCHEMA="$AI_REVIEW_ROOT_DIR/schemas/raw_finding_batch.schema.json"
  fi
}

# Copy the pinned MR snapshot into $1. The reviewed files must sit at the
# working-tree root or paths from the diff (src/foo.py) will not resolve. The
# copy keeps CLI writes out of the shared input bundle, and keeps the input
# directory off the path a CLI walks upward to find project config. Prepare
# already dropped the agent files every CLI reads (_SNAPSHOT_IGNORE_ALWAYS in
# input_bundle.py).
#   copy_review_snapshot <root>
copy_review_snapshot() {
  review_root="$1"
  snapshot="$AI_REVIEW_INPUT_DIR/repo_snapshot"
  if [ ! -d "$snapshot" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  cp -R "$snapshot"/. "$review_root"/
  unset review_root snapshot
}
