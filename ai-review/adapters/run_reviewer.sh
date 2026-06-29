#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: run_reviewer.sh <reviewer> <review|critique|respond>" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON:-python3}"
exec "$PYTHON_BIN" -m ai_review.adapter_runner "$1" "$2"
