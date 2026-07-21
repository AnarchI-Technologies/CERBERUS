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
RELEASE="/opt/cerberus-releases/$COMMIT"
STAGING_LINK="/opt/cerberus-staging-current"
PRODUCTION_LINK="/opt/cerberus-current"
SERVICE="cerberus-staging.service"
HEALTH_URL="http://127.0.0.1:10001/"

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

echo "=== ACTIVATE CERBERUS STAGING ==="
echo

if [ ! -d "$RELEASE" ]; then
    echo "Release does not exist: $RELEASE"
    exit 1
fi

for file in \
    "$RELEASE/requirements.txt" \
    "$RELEASE/src/render_app.py" \
    "$RELEASE/src/claw_runtime.py"
do
    if [ ! -f "$file" ]; then
        echo "Required file is missing: $file"
        exit 1
    fi
done

OLD_STAGING="$(readlink -f "$STAGING_LINK" 2>/dev/null || true)"
PRODUCTION="$(readlink -f "$PRODUCTION_LINK" 2>/dev/null || true)"

echo "Candidate:"
echo "$RELEASE"

echo
echo "Current staging:"
echo "${OLD_STAGING:-UNRESOLVED}"

echo
echo "Production remains:"
echo "${PRODUCTION:-UNRESOLVED}"

if [ "$OLD_STAGING" = "$RELEASE" ]; then
    echo
    echo "Staging already points to this release."
else
    echo
    echo "Switching staging atomically..."

    sudo ln -sfn "$RELEASE" "${STAGING_LINK}.next"
    sudo mv -Tf "${STAGING_LINK}.next" "$STAGING_LINK"

    test "$(readlink -f "$STAGING_LINK")" = "$RELEASE"
fi

rollback_staging() {
    echo
    echo "Staging validation failed."

    if [ -n "$OLD_STAGING" ] && [ -d "$OLD_STAGING" ]; then
        echo "Rolling staging back to:"
        echo "$OLD_STAGING"

        sudo ln -sfn "$OLD_STAGING" "${STAGING_LINK}.rollback"
        sudo mv -Tf "${STAGING_LINK}.rollback" "$STAGING_LINK"
        sudo systemctl restart "$SERVICE" || true
    else
        echo "No valid previous staging target was available."
    fi
}

echo
echo "Restarting staging..."
sudo systemctl restart "$SERVICE"
sleep 3

if ! sudo systemctl is-active --quiet "$SERVICE"; then
    rollback_staging
    exit 1
fi

if ! curl \
    --fail \
    --silent \
    --show-error \
    --max-time 10 \
    "$HEALTH_URL" \
    >/dev/null
then
    rollback_staging
    exit 1
fi

echo
echo "=== STAGING SUMMARY ==="
echo "staging=$(readlink -f "$STAGING_LINK")"
echo "production=$(readlink -f "$PRODUCTION_LINK")"
echo "service=$(sudo systemctl is-active "$SERVICE")"
echo "http=healthy"

echo
echo "STAGING_ACTIVATION_PASSED"
