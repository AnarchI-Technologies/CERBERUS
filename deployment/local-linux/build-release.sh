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

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"

REPO_ROOT="$(
    cd -- "$SCRIPT_DIR/../.."
    pwd
)"

RELEASE_ROOT="/opt/cerberus-releases"
RELEASE="$RELEASE_ROOT/$COMMIT"
TEMP_ROOT="$(mktemp -d /tmp/cerberus-release-build.XXXXXX)"
TEMP_ARCHIVE="$TEMP_ROOT/$COMMIT.tar"
TEMP_RELEASE="$TEMP_ROOT/release"

cleanup() {
    rm -rf "$TEMP_ROOT"
}

trap cleanup EXIT

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

echo "=== BUILD CERBERUS IMMUTABLE RELEASE ==="
echo
echo "Repository:"
echo "$REPO_ROOT"

echo
echo "Commit:"
echo "$COMMIT"

echo
echo "Target:"
echo "$RELEASE"

if ! git -C "$REPO_ROOT" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
    echo
    echo "Commit does not exist in the repository:"
    echo "$COMMIT"
    exit 1
fi

RESOLVED_COMMIT="$(git -C "$REPO_ROOT" rev-parse "$COMMIT^{commit}")"

if [ "$RESOLVED_COMMIT" != "$COMMIT" ]; then
    echo
    echo "Resolved commit does not match the requested commit."
    exit 1
fi

if sudo test -e "$RELEASE"; then
    echo
    echo "Release already exists."

    for file in \
        "$RELEASE/requirements.txt" \
        "$RELEASE/src/render_app.py" \
        "$RELEASE/src/claw_runtime.py"
    do
        if ! sudo test -f "$file"; then
            echo "Existing release is incomplete:"
            echo "$file"
            exit 1
        fi
    done

    echo
    echo "RELEASE_ALREADY_EXISTS"
    exit 0
fi

mkdir -p "$TEMP_RELEASE"

echo
echo "Creating Git archive..."

git -C "$REPO_ROOT" archive \
    --format=tar \
    --output="$TEMP_ARCHIVE" \
    "$COMMIT"

test -s "$TEMP_ARCHIVE"

echo "Extracting candidate release..."

tar -xf "$TEMP_ARCHIVE" -C "$TEMP_RELEASE"

for file in \
    "$TEMP_RELEASE/requirements.txt" \
    "$TEMP_RELEASE/src/render_app.py" \
    "$TEMP_RELEASE/src/claw_runtime.py" \
    "$TEMP_RELEASE/deployment/local-linux/cerberusctl"
do
    if [ ! -f "$file" ]; then
        echo
        echo "Candidate release is missing required file:"
        echo "$file"
        exit 1
    fi
done

BRANCH="$(
    git -C "$REPO_ROOT" branch \
        --all \
        --contains "$COMMIT" \
        --format='%(refname:short)' |
        head -1
)"

BUILT_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
PYTHON_VERSION="$(/opt/cerberus-venv/bin/python --version 2>&1)"
FILE_COUNT="$(find "$TEMP_RELEASE" -type f -print | awk 'END { print NR + 0 }')"

cat >"$TEMP_RELEASE/release.json" <<EOF
{
  "commit": "$COMMIT",
  "branch": "${BRANCH:-unknown}",
  "built_at": "$BUILT_AT",
  "python": "$PYTHON_VERSION",
  "files": $FILE_COUNT,
  "verification": "pending"
}
EOF

echo
echo "Installing immutable release atomically..."

sudo install -d -m 0755 "$RELEASE_ROOT"

INSTALL_TARGET="${RELEASE}.installing"

sudo rm -rf "$INSTALL_TARGET"
sudo install -d -m 0755 "$INSTALL_TARGET"
sudo cp -a "$TEMP_RELEASE/." "$INSTALL_TARGET/"

sudo chown -R root:root "$INSTALL_TARGET"
sudo find "$INSTALL_TARGET" -type d -exec chmod 0755 {} +
sudo find "$INSTALL_TARGET" -type f -exec chmod 0644 {} +

if sudo test -d "$INSTALL_TARGET/deployment/local-linux"; then
    sudo find \
        "$INSTALL_TARGET/deployment/local-linux" \
        -type f \
        \( -name '*.sh' -o -name 'cerberusctl' \) \
        -exec chmod 0755 {} +
fi

sudo mv "$INSTALL_TARGET" "$RELEASE"

test "$(readlink -f "$RELEASE")" = "$RELEASE"

echo
echo "=== RELEASE BUILD SUMMARY ==="
echo "release=$RELEASE"
echo "commit=$COMMIT"
echo "branch=${BRANCH:-unknown}"
echo "files=$FILE_COUNT"
echo "built_at=$BUILT_AT"

echo
echo "RELEASE_BUILD_PASSED"
