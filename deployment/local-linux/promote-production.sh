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
PRODUCTION_LINK="/opt/cerberus-current"
STAGING_LINK="/opt/cerberus-staging-current"
SERVICE="cerberus.service"
HEALTH_URL="http://127.0.0.1:10000/"
ROLLBACK_FILE="/var/lib/cerberus/previous-production-release"

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

echo "=== PROMOTE CERBERUS PRODUCTION ==="
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

STAGING_RELEASE="$(readlink -f "$STAGING_LINK" 2>/dev/null || true)"
OLD_PRODUCTION="$(readlink -f "$PRODUCTION_LINK" 2>/dev/null || true)"

echo "Candidate:"
echo "$RELEASE"

echo
echo "Staging:"
echo "${STAGING_RELEASE:-UNRESOLVED}"

echo
echo "Current production:"
echo "${OLD_PRODUCTION:-UNRESOLVED}"

if [ "$STAGING_RELEASE" != "$RELEASE" ]; then
    echo
    echo "Promotion blocked: staging does not point to the candidate release."
    exit 1
fi

if [ -z "$OLD_PRODUCTION" ] || [ ! -d "$OLD_PRODUCTION" ]; then
    echo
    echo "Unable to resolve a valid current production release."
    exit 1
fi

if [ "$OLD_PRODUCTION" = "$RELEASE" ]; then
    echo
    echo "Production already points to this release."

    sudo systemctl is-active --quiet "$SERVICE"

    curl \
        --fail \
        --silent \
        --show-error \
        --max-time 10 \
        "$HEALTH_URL" \
        >/dev/null

    echo
    echo "PRODUCTION_PROMOTION_PASSED"
    exit 0
fi

rollback_production() {
    echo
    echo "Production validation failed."
    echo "Rolling back to:"
    echo "$OLD_PRODUCTION"

    sudo ln -sfn "$OLD_PRODUCTION" "${PRODUCTION_LINK}.rollback"
    sudo mv -Tf "${PRODUCTION_LINK}.rollback" "$PRODUCTION_LINK"
    sudo systemctl restart "$SERVICE" || true
}

echo
echo "Saving rollback target..."

sudo install -d -m 0755 "$(dirname "$ROLLBACK_FILE")"
printf '%s\n' "$OLD_PRODUCTION" |
    sudo tee "$ROLLBACK_FILE" >/dev/null

echo "Rollback target:"
sudo cat "$ROLLBACK_FILE"

echo
echo "Switching production atomically..."

sudo ln -sfn "$RELEASE" "${PRODUCTION_LINK}.next"
sudo mv -Tf "${PRODUCTION_LINK}.next" "$PRODUCTION_LINK"

test "$(readlink -f "$PRODUCTION_LINK")" = "$RELEASE"

echo
echo "Restarting production..."
sudo systemctl restart "$SERVICE"
sleep 3

if ! sudo systemctl is-active --quiet "$SERVICE"; then
    rollback_production
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
    rollback_production
    exit 1
fi

echo
echo "=== PRODUCTION SUMMARY ==="
echo "production=$(readlink -f "$PRODUCTION_LINK")"
echo "staging=$(readlink -f "$STAGING_LINK")"
echo "rollback=$(sudo cat "$ROLLBACK_FILE")"
echo "service=$(sudo systemctl is-active "$SERVICE")"
echo "http=healthy"

echo
echo "PRODUCTION_PROMOTION_PASSED"
