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
# neither the adapter nor opencode serve is required to create it.
mkdir -p "$smoke_dir/home"

# The reviewer image ships the driver at /opt/scripts (base.Dockerfile). Running it
# inside the image keeps the probe on the pinned opencode, the pinned rg, the
# shipped adapter and the shipped client instead of the host's.
#
# --network none is load-bearing twice over: the stub provider is on loopback, which
# survives `none`, so a regression that reaches a real endpoint fails here instead of
# spending a token; and a reintroduced review-time ripgrep download cannot succeed.
timeout 420 docker run --rm \
  --network none \
  --mount "type=bind,src=$smoke_dir,dst=/smoke" \
  --env HOME=/smoke/home \
  --env PATH=/usr/local/bin:/usr/bin:/bin \
  "$image" \
  python3 /opt/scripts/smoke_opencode_structured_output.py
