#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    echo "Usage: $0 <full-commit-hash>"
}

if [ "$#" -ne 1 ]; then
    usage
    exit 64
fi

COMMIT="$1"
RELEASE_ROOT="/opt/cerberus-releases"
RELEASE="$RELEASE_ROOT/$COMMIT"
PYTHON="/opt/cerberus-venv/bin/python"
MANIFEST="$RELEASE/release.json"

case "$COMMIT" in
    *[!0-9a-f]*|"")
        echo "Invalid commit hash: $COMMIT"
        exit 64
        ;;
esac

if [ "${#COMMIT}" -ne 40 ]; then
    echo "A full 40-character commit hash is required."
    exit 64
fi

echo "=== VERIFY IMMUTABLE RELEASE ==="
echo
echo "Commit:"
echo "$COMMIT"

echo
echo "Release:"
echo "$RELEASE"

if [ ! -d "$RELEASE" ]; then
    echo "Release directory does not exist."
    exit 1
fi

REQUIRED_FILES=(
    "$RELEASE/requirements.txt"
    "$RELEASE/src/render_app.py"
    "$RELEASE/src/claw_runtime.py"
    "$RELEASE/src/claw_config.py"
)

echo
echo "=== STRUCTURE CHECK ==="

for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "MISSING: $file"
        exit 1
    fi

    echo "FOUND: $file"
done

FILE_COUNT="$(
    find "$RELEASE" -type f -print |
        awk 'END { print NR + 0 }'
)"

echo
echo "Files:"
echo "$FILE_COUNT"

echo
echo "=== PYTHON COMPILE ==="

cd "$RELEASE"

PYTHONPATH="$RELEASE/src:$RELEASE/data:$RELEASE" \
"$PYTHON" -m compileall -q \
    src \
    data \
    tests \
    combat_decider.py \
    death_zone_engine.py \
    ep_economy_engine.py \
    free_action_abuse.py \
    main_loop.py \
    memory_system.py \
    predator_mode.py \
    quickstart.py \
    sitecustomize.py \
    threat_engine.py

echo "COMPILE_PASSED"

echo
echo "=== TEST SUITE ==="

TEST_OUTPUT="$(
    PYTHONPATH="$RELEASE/src:$RELEASE/data:$RELEASE" \
    "$PYTHON" -m unittest discover \
        -s tests \
        -p 'test_*.py' \
        -v 2>&1
)"

printf '%s\n' "$TEST_OUTPUT"

if ! grep -Eq '^OK( |$|\()' <<<"$TEST_OUTPUT"; then
    echo
    echo "TEST_SUITE_FAILED"
    exit 1
fi

TEST_COUNT="$(
    sed -nE 's/^Ran ([0-9]+) tests?.*/\1/p' <<<"$TEST_OUTPUT" |
        tail -1
)"

SKIPPED_COUNT="$(
    sed -nE 's/^OK.*skipped=([0-9]+).*/\1/p' <<<"$TEST_OUTPUT" |
        tail -1
)"

if [ -z "$TEST_COUNT" ]; then
    echo "Unable to determine the test count."
    exit 1
fi

SKIPPED_COUNT="${SKIPPED_COUNT:-0}"
VERIFIED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
PYTHON_VERSION="$("$PYTHON" --version 2>&1)"
HOSTNAME_VALUE="$(hostname)"

BRANCH="unknown"
BUILT_AT="unknown"

if [ -f "$MANIFEST" ]; then
    BRANCH="$(
        sed -nE 's/^[[:space:]]*"branch":[[:space:]]*"([^"]+)".*/\1/p' \
            "$MANIFEST" |
            head -1
    )"

    BUILT_AT="$(
        sed -nE 's/^[[:space:]]*"built_at":[[:space:]]*"([^"]+)".*/\1/p' \
            "$MANIFEST" |
            head -1
    )"

    BRANCH="${BRANCH:-unknown}"
    BUILT_AT="${BUILT_AT:-unknown}"
fi

TEMP_MANIFEST="$(mktemp)"

cat >"$TEMP_MANIFEST" <<EOF
{
  "commit": "$COMMIT",
  "branch": "$BRANCH",
  "built_at": "$BUILT_AT",
  "verified_at": "$VERIFIED_AT",
  "verified_on": "$HOSTNAME_VALUE",
  "python": "$PYTHON_VERSION",
  "files": $FILE_COUNT,
  "tests": {
    "run": $TEST_COUNT,
    "passed": $TEST_COUNT,
    "failed": 0,
    "skipped": $SKIPPED_COUNT
  },
  "verification": "passed"
}
EOF

sudo install \
    -o root \
    -g root \
    -m 0644 \
    "$TEMP_MANIFEST" \
    "$MANIFEST"

rm -f "$TEMP_MANIFEST"

echo
echo "=== VERIFICATION SUMMARY ==="
echo "release=$RELEASE"
echo "files=$FILE_COUNT"
echo "tests=$TEST_COUNT"
echo "skipped=$SKIPPED_COUNT"
echo "manifest=$MANIFEST"
echo "result=passed"

echo
echo "RELEASE_VERIFICATION_PASSED"
