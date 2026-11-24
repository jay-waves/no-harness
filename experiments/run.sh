#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=$(dirname "$here")
work="$here/.work"
results="$here/results"
mkdir -p "$work" "$results"

case "${1:-}" in
  loopback|serialization)
    case "$1" in
      loopback) test_name=TestExecutionPilot ;;
      serialization) test_name=TestSerializationPilot ;;
    esac
    overlay=$(mktemp)
    trap 'rm -f "$overlay"' EXIT
    python3 - "$repo" "$here" "$1" > "$overlay" <<'PY'
import json
import sys
from pathlib import Path

repo, experiments = map(Path, sys.argv[1:3])
source = sys.argv[3] + "_test.go"
print(json.dumps({"Replace": {
    str(repo / "ssh-mcp" / source): str(experiments / source),
}}))
PY
    cd "$repo/ssh-mcp"
    NO_HARNESS_PILOT_OUTPUT="$results/loopback.json" \
    ONE_SERIALIZATION_OUTPUT="$results/serialization.json" \
      go test -overlay "$overlay" -mod=readonly -run "^$test_name\$" -count=1 -v .
    ;;
  footprint)
    archive="$work/codex.tar.gz"
    release='https://github.com/openai/codex/releases/download/rust-v0.170.0'
    if [ ! -f "$archive" ]; then
      curl -fL "$release/codex-package-x86_64-unknown-linux-musl.tar.gz" -o "$archive.part"
      mv "$archive.part" "$archive"
    fi
    printf '%s  %s\n' 'a77ff87be7ba809d715728270a78f5ce3527dc07529416d1ef8929b90b8475eb' "$archive" | sha256sum -c -
    mkdir -p "$work/codex-package"
    tar -xzf "$archive" -C "$work/codex-package"
    (cd "$repo/ssh-mcp" && CGO_ENABLED=0 go build -mod=readonly -trimpath -ldflags='-s -w' -o "$work/one" .)

    docker build --build-arg 'BUILD_TOOLS=gcc musl-dev' -t no-harness-footprint:base - < "$here/Dockerfile"
    docker build -t no-harness-footprint:python - < "$here/Dockerfile"
    docker run --rm --network none -v "$work/codex-package:/opt/codex:ro,Z" \
      no-harness-footprint:python /opt/codex/bin/codex --version
    for profile in base python; do
      python3 "$here/footprint.py" "$profile"
    done
    python3 "$here/sizes.py" > "$results/footprint.json"

    : > "$results/memory.jsonl"
    for round in 1 2 3 4 5; do
      for mode in codex worker; do
        docker run --rm --network none \
          -v "$here/memory.py:/tmp/memory.py:ro,Z" \
          -v "$work/codex-package:/opt/codex:ro,Z" \
          -v "$work/one:/usr/local/bin/one:ro,Z" \
          no-harness-footprint:python python3 /tmp/memory.py "$mode" >> "$results/memory.jsonl"
      done
    done
    ;;
  *)
    printf 'Usage: %s {loopback|footprint|serialization}\n' "$0" >&2
    exit 2
    ;;
esac
