#!/bin/sh
set -eu

# Render's Docker command field handles this script path reliably; keep shell
# sequencing here instead of embedding a quoted command in render.yaml.
python -m app.runtime_entry migrate

python -m app.runtime_entry worker &
worker_pid=$!

cleanup() {
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

python -m app.runtime_entry api
