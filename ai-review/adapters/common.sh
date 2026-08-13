# shellcheck shell=sh
# Scaffolding shared by every reviewer adapter.
#
# Sourced, not executed. Each adapter sets REQUIRE_REAL and then sources this
# file, which is why the mock gate lives here rather than being repeated four
# times: a new seat that forgets it would silently ignore AI_REVIEW_LOCAL_MOCK.
#
# What stays in the individual adapters is what actually differs between the
# CLIs — their flags, their configuration files, their credential names, and the
# way each one is told to emit structured output.

# `$0` is the sourcing adapter under POSIX sh, so this resolves relative to the
# adapter rather than to this file. Only shell builtins are used: adapters are
# also exercised through `env -i` with a stripped PATH, where dirname is absent,
# and the mock fallback below has to keep working there.
AI_REVIEW_ROOT_DIR="$(CDPATH= cd -- "${0%/*}/.." && pwd)"

# Replace this process with the deterministic mock reviewer.
#
# The second gate is deliberate. AI_REVIEW_LOCAL_MOCK alone is set by the local
# harness and could also be set as a GitLab project variable, which would
# silently mock a production review; requiring AI_REVIEW_ALLOW_LOCAL_MOCK too
# means a stray project variable fails loudly instead. adapter_process.py
# enforces the same pair before this script is ever reached.
run_mock() {
  if [ "${AI_REVIEW_ALLOW_LOCAL_MOCK:-}" != "true" ]; then
    echo "mock reviewer fallback requires AI_REVIEW_ALLOW_LOCAL_MOCK=true" >&2
    exit 2
  fi
  exec "${PYTHON:-python3}" -m ai_review.mock_reviewer "$AI_REVIEW_REVIEWER" "$AI_REVIEW_STAGE"
}

# Honor an explicit mock request unless this seat demands a real CLI.
mock_if_requested() {
  if [ "${REQUIRE_REAL:-}" != "1" ] && [ "${AI_REVIEW_LOCAL_MOCK:-}" = "1" ]; then
    run_mock
  fi
}

# A precondition for running the real CLI is unmet: fail closed when this seat
# demands a real run, otherwise fall back to the mock.
#   require_real_or_mock <message> [exit-code]
require_real_or_mock() {
  if [ "${REQUIRE_REAL:-}" = "1" ]; then
    echo "$1" >&2
    exit "${2:-2}"
  fi
  run_mock
}

# Reject an empty AI_REVIEW_MODEL. Unlike a missing CLI this is a configuration
# error on every seat, so it never falls back to the mock.
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

# Copy the pinned MR snapshot into $1 and strip project-level agent
# configuration named by the remaining arguments.
#
# The reviewed files must sit at the working-tree root or paths from the diff
# (src/foo.py) will not resolve. Stripping is by name at every level, not just
# the root, because these files are resolved hierarchically; symlinks match too,
# or a symlinked AGENTS.md pointing outside the snapshot would survive and still
# be followed. Directories are removed with -prune.
#   prepare_review_root <root> <name>...
prepare_review_root() {
  review_root="$1"
  shift
  snapshot="$AI_REVIEW_INPUT_DIR/repo_snapshot"
  if [ ! -d "$snapshot" ]; then
    echo "AI review repo_snapshot is required for the $AI_REVIEW_REVIEWER reviewer" >&2
    exit 2
  fi
  cp -R "$snapshot"/. "$review_root"/
  for name in "$@"; do
    # A trailing slash marks a directory to remove recursively (".claude/").
    if [ "${name%/}" != "$name" ]; then
      find "$review_root" -name "${name%/}" -prune -exec rm -rf {} +
    else
      find "$review_root" -name "$name" \( -type f -o -type l \) -delete
    fi
  done
  unset review_root snapshot name
}
