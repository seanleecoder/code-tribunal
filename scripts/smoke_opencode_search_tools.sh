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

# HOME must exist before the container starts: it lives on the bind mount, and
# opencode serve is not required to create its own state directory.
mkdir -p "$smoke_dir/home"

# The reviewer image ships the driver at /opt/scripts (base.Dockerfile). Running
# it inside the image keeps the probe on the pinned binaries and PYTHONPATH
# instead of the host's. --read-only is avoided because opencode serve writes
# state under its isolated HOME on the mounted /smoke.
#
# --network none is load-bearing, not hygiene: the defect this probe guards is
# OpenCode fetching an unverified ripgrep at review time. With egress removed, a
# regression that restores that fetch fails here instead of quietly succeeding.
# Nothing the probe needs crosses the network — loopback survives `none`, the
# model-list fetch is disabled, and no provider is contacted.
timeout 300 docker run --rm \
  --network none \
  --mount "type=bind,src=$smoke_dir,dst=/smoke" \
  --env HOME=/smoke/home \
  --env PATH=/usr/local/bin:/usr/bin:/bin \
  "$image" \
  python3 /opt/scripts/smoke_opencode_search_tools.py
