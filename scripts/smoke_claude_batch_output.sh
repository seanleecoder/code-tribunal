#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reviewer-image>" >&2
  exit 2
fi
image="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
driver="$repo_root/scripts/smoke_claude_batch_output.py"

# Loopback survives --network none. Real pinned CLI, registry argv, runner and
# admission; the image's own PATH and uid; no secrets or paid provider.
# Scratch is a container-local tmpfs that Docker removes with the container, so
# a rootful daemon leaves no root-owned files on the host. Every result is
# printed; the host reads nothing back.
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
    --mount "type=tmpfs,dst=/smoke,tmpfs-mode=1777" \
    --mount "type=bind,src=$driver,dst=/probe/smoke_claude_batch_output.py,readonly" \
    --env HOME=/smoke \
    "$image" \
    python3 /probe/smoke_claude_batch_output.py --profile "$profile"
}
status=0
run_profile stock || status=1
run_profile faults || status=1
exit "$status"
