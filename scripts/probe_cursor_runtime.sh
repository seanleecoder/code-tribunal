#!/bin/sh
set -eu

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <reviewer-image> <cursor-model> <output-dir> <identity-file>" >&2
  exit 2
fi
image="$1"
model="$2"
out="$3"
identity="$4"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
driver="$repo_root/scripts/probe_cursor_runtime.py"

if [ -z "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is required" >&2
  exit 2
fi

# The same unprivileged container a review job gets: the image's own uid and
# PATH and Docker's default security options, with network for Cursor's
# backend. The key is passed by name, never on the command line. Nothing on the
# runner is writable from the container. The driver streams completed results to
# private files and collect validates and redacts them into the upload directory.
work="$(mktemp -d)"
container="cursor-probe-${work##*/}"

remove_container() {
  timeout -k 5 15 docker rm -f "$container" >/dev/null 2>&1 || :
}

cleanup() {
  remove_container
  rm -rf "$work"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
container_status=0
timeout -k 30 3900 docker run --rm --name "$container" --init --pull=never \
  --mount "type=tmpfs,dst=/probe-scratch,tmpfs-mode=1777" \
  --mount "type=bind,src=$driver,dst=/probe/probe_cursor_runtime.py,readonly" \
  --env CURSOR_API_KEY \
  --env HOME=/probe-scratch \
  --env TMPDIR=/probe-scratch \
  "$image" \
  python3 /probe/probe_cursor_runtime.py run --model "$model" --scratch /probe-scratch \
  >"$work/results.jsonl" 2>"$work/container.stderr" || container_status=$?
case "$container_status" in
  124|137) remove_container ;;
esac

# The last command's status is the script's; the EXIT trap still cleans up.
PYTHONPATH="$repo_root/ai-review/src" python3 -B "$driver" collect \
  --results "$work/results.jsonl" \
  --container-stderr "$work/container.stderr" \
  --container-exit-code "$container_status" \
  --identity-file "$identity" \
  --image "$image" \
  --out "$out"
