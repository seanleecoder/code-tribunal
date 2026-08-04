#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reviewer-image>" >&2
  exit 2
fi
image="$1"

smoke_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$smoke_dir" 2>/dev/null || true
}
trap cleanup EXIT

# The reviewer image ships the driver at /opt/scripts (base.Dockerfile). Running
# it inside the image keeps the probe on the pinned binaries and PYTHONPATH
# instead of the host's. --read-only is avoided because opencode serve writes
# state under its isolated HOME on the mounted /smoke.
timeout 240 docker run --rm \
  --mount "type=bind,src=$smoke_dir,dst=/smoke" \
  --env HOME=/smoke/home \
  --env PATH=/usr/local/bin:/usr/bin:/bin \
  "$image" \
  python3 /opt/scripts/smoke_opencode_search_tools.py
