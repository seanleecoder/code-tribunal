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
# uid 0 without DAC_OVERRIDE (the fault run) must still write here.
chmod 0777 "$smoke_dir" "$smoke_dir/home"

# Loopback survives --network none. Real pinned CLI, host, sandbox, registry and
# adapter; no secrets or access to a paid provider. The driver never ships.
run_driver() {
  if [ "$1" = faults ]; then
    # Only the two DAC bypass capabilities are dropped, so uid 0 cannot read
    # the mode-000 sentinel in each fault arm's copied snapshot.
    set -- --cap-drop DAC_OVERRIDE --cap-drop DAC_READ_SEARCH
    arguments="--faults --scratch /smoke/codex-faults"
  else
    set --
    arguments=""
  fi
  # $arguments holds fixed words; splitting it is intended.
  # shellcheck disable=SC2086
  timeout 900 docker run --rm \
    --network none \
    "$@" \
    --mount "type=bind,src=$smoke_dir,dst=/smoke" \
    --mount "type=bind,src=$driver,dst=/probe/smoke_codex_batch_output.py,readonly" \
    --env HOME=/smoke/home \
    --env PATH=/usr/local/bin:/usr/bin:/bin \
    "$image" \
    python3 /probe/smoke_codex_batch_output.py $arguments
}
run_driver suite
run_driver faults
