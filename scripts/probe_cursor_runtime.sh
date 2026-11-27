#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <reviewer-image> <cursor-model> <output-dir>" >&2
  exit 2
fi
image="$1"
model="$2"
out="$3"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
driver="$repo_root/scripts/probe_cursor_runtime.py"

if [ -z "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is required" >&2
  exit 2
fi

# The same unprivileged container a review job gets: the image's own uid and
# PATH and Docker's default security options, with network for Cursor's
# backend. The key is passed by name, never on the command line. Scratch is a
# container-local tmpfs; only the redacted results land in the output directory.
mkdir -p "$out"
chmod 0777 "$out"
timeout 3600 docker run --rm \
  --mount "type=tmpfs,dst=/probe-scratch,tmpfs-mode=1777" \
  --mount "type=bind,src=$driver,dst=/probe/probe_cursor_runtime.py,readonly" \
  --mount "type=bind,src=$out,dst=/probe-out" \
  --env CURSOR_API_KEY \
  --env HOME=/probe-scratch \
  "$image" \
  python3 /probe/probe_cursor_runtime.py \
    --model "$model" --scratch /probe-scratch --out /probe-out
