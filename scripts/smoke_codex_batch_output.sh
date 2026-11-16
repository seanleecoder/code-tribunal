#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reviewer-image>" >&2
  exit 2
fi
image="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
driver="$repo_root/scripts/smoke_codex_batch_output.py"
smoke_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$smoke_dir" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$smoke_dir/home"

# Loopback survives --network none. Real pinned CLI, host, sandbox, registry and
# adapter; no secrets or access to a paid provider. The driver never ships.
timeout 900 docker run --rm \
  --network none \
  --mount "type=bind,src=$smoke_dir,dst=/smoke" \
  --mount "type=bind,src=$driver,dst=/probe/smoke_codex_batch_output.py,readonly" \
  --env HOME=/smoke/home \
  --env PATH=/usr/local/bin:/usr/bin:/bin \
  "$image" \
  python3 /probe/smoke_codex_batch_output.py
