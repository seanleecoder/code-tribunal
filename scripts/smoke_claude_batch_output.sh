#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reviewer-image>" >&2
  exit 2
fi
image="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
driver="$repo_root/scripts/smoke_claude_batch_output.py"
smoke_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$smoke_dir" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$smoke_dir/home"
# uid 0 without DAC_OVERRIDE (the faults profile) must still write here.
chmod 0777 "$smoke_dir" "$smoke_dir/home"

# Loopback survives --network none. Real pinned CLI, registry argv, runner and
# admission; the image's own PATH and uid; no secrets or paid provider.
run_profile() {
  profile="$1"
  if [ "$profile" = faults ]; then
    # Only the two DAC bypass capabilities are dropped, so uid 0 cannot read
    # the mode-000 sentinel in each fault case's copied snapshot.
    set -- --cap-drop DAC_OVERRIDE --cap-drop DAC_READ_SEARCH
  else
    set --
  fi
  timeout 900 docker run --rm \
    --network none \
    "$@" \
    --mount "type=bind,src=$smoke_dir,dst=/smoke" \
    --mount "type=bind,src=$driver,dst=/probe/smoke_claude_batch_output.py,readonly" \
    --env HOME=/smoke/home \
    "$image" \
    python3 /probe/smoke_claude_batch_output.py --profile "$profile"
}
status=0
run_profile stock || status=1
run_profile faults || status=1
exit "$status"
