#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCTION_LINK="/opt/cerberus-current"
SERVICE="cerberus.service"
HEALTH_URL="http://127.0.0.1:10000/"
ROLLBACK_FILE="/var/lib/cerberus/previous-production-release"

echo "=== ROLLBACK CERBERUS PRODUCTION ==="
echo

if ! sudo test -f "$ROLLBACK_FILE"; then
    echo "Rollback record does not exist:"
    echo "$ROLLBACK_FILE"
    exit 1
fi

CURRENT_RELEASE="$(readlink -f "$PRODUCTION_LINK" 2>/dev/null || true)"
ROLLBACK_RELEASE="$(sudo cat "$ROLLBACK_FILE" | tr -d '\r\n')"

echo "Current production:"
echo "${CURRENT_RELEASE:-UNRESOLVED}"

echo
echo "Recorded rollback target:"
echo "${ROLLBACK_RELEASE:-EMPTY}"

if [ -z "$CURRENT_RELEASE" ] || [ ! -d "$CURRENT_RELEASE" ]; then
    echo
    echo "Current production release could not be resolved."
    exit 1
fi

if [ -z "$ROLLBACK_RELEASE" ] || [ ! -d "$ROLLBACK_RELEASE" ]; then
    echo
    echo "Recorded rollback release does not exist."
    exit 1
fi

if [ "$CURRENT_RELEASE" = "$ROLLBACK_RELEASE" ]; then
    echo
    echo "Production already points to the recorded rollback release."

    sudo systemctl is-active --quiet "$SERVICE"

    curl \
        --fail \
        --silent \
        --show-error \
        --max-time 10 \
        "$HEALTH_URL" \
        >/dev/null

    echo
    echo "ROLLBACK_NOT_REQUIRED"
    exit 0
fi

for file in \
    "$ROLLBACK_RELEASE/requirements.txt" \
    "$ROLLBACK_RELEASE/src/render_app.py" \
    "$ROLLBACK_RELEASE/src/claw_runtime.py"
do
    if [ ! -f "$file" ]; then
        echo
        echo "Rollback target is missing required file:"
        echo "$file"
        exit 1
    fi
done

restore_current_release() {
    echo
    echo "Rollback validation failed."
    echo "Restoring the release that was active before rollback:"
    echo "$CURRENT_RELEASE"

    sudo ln -sfn "$CURRENT_RELEASE" "${PRODUCTION_LINK}.restore"
    sudo mv -Tf "${PRODUCTION_LINK}.restore" "$PRODUCTION_LINK"
    sudo systemctl restart "$SERVICE" || true
}

echo
echo "Switching production to rollback release..."

sudo ln -sfn "$ROLLBACK_RELEASE" "${PRODUCTION_LINK}.rollback"
sudo mv -Tf "${PRODUCTION_LINK}.rollback" "$PRODUCTION_LINK"

test "$(readlink -f "$PRODUCTION_LINK")" = "$ROLLBACK_RELEASE"

echo
echo "Restarting production..."
sudo systemctl restart "$SERVICE"
sleep 3

if ! sudo systemctl is-active --quiet "$SERVICE"; then
    restore_current_release
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
    restore_current_release
    exit 1
fi

echo
echo "Updating rollback record to the release just replaced..."

printf '%s\n' "$CURRENT_RELEASE" |
    sudo tee "$ROLLBACK_FILE" >/dev/null

echo
echo "=== ROLLBACK SUMMARY ==="
echo "production=$(readlink -f "$PRODUCTION_LINK")"
echo "next_rollback=$(sudo cat "$ROLLBACK_FILE")"
echo "service=$(sudo systemctl is-active "$SERVICE")"
echo "http=healthy"

echo
echo "PRODUCTION_ROLLBACK_PASSED"
