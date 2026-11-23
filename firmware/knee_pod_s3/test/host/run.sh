#!/usr/bin/env sh
# Host-side unit test for lib/pod_core (no hardware, no Arduino).
# Usage: sh firmware/knee_pod_s3/test/host/run.sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
POD=$(CDPATH= cd -- "$HERE/../../lib/pod_core" && pwd)
OUT="$HERE/build"
mkdir -p "$OUT"
CFLAGS="-std=c99 -Wall -Wextra -Wpedantic -Werror -O1 -g"
cc $CFLAGS -I "$POD/include" -I "$HERE" "$POD/src/pod_core.c" "$HERE/test_pod_core.c" -lm -o "$OUT/test_pod_core"
"$OUT/test_pod_core"
