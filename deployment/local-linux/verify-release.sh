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

if [ -z "$TEST_COUNT" ]; then
    echo "Unable to determine the test count."
    exit 1
fi

echo
echo "=== VERIFICATION SUMMARY ==="
echo "release=$RELEASE"
echo "files=$FILE_COUNT"
echo "tests=$TEST_COUNT"
echo "result=passed"

echo
echo "RELEASE_VERIFICATION_PASSED"
