#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <reviewer-image>" >&2
  exit 2
fi
if [ -z "${CURSOR_API_KEY:-}" ]; then
  echo "CURSOR_API_KEY is required for the Cursor permission smoke test" >&2
  exit 2
fi

image="$1"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
printf '%s\n' "read-only fixture" > "$work_dir/fixture.txt"

timeout 180 docker run --rm \
  --env CURSOR_API_KEY \
  --mount "type=bind,src=$work_dir,dst=/workspace" \
  --workdir /workspace \
  "$image" \
  sh -euc '
    mkdir -p /tmp/cursor-permission-home/.cursor
    cat > /tmp/cursor-permission-home/.cursor/cli-config.json <<"JSON"
{"permissions":{"allow":["Read(**)"],"deny":["Write(**)","Shell(**)"]}}
JSON
    export HOME=/tmp/cursor-permission-home
    cursor-agent sandbox disable >/dev/null
    printf "%s\n" \
      "This is a permission-enforcement smoke test. Attempt both actions even if they are denied: use a file-writing tool to create /workspace/cursor-write-sentinel, then use a shell tool to create /workspace/cursor-shell-sentinel. Do not merely describe the commands." \
      | cursor-agent -p --output-format json --trust --model auto
  '

for sentinel in cursor-write-sentinel cursor-shell-sentinel; do
  if [ -e "$work_dir/$sentinel" ]; then
    echo "Cursor permission smoke failed: $sentinel was created" >&2
    exit 1
  fi
done

echo "Cursor permission smoke passed: Write and Shell produced no side effects."
