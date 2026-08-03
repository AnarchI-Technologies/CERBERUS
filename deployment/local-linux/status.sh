#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCTION_LINK="/opt/cerberus-current"
STAGING_LINK="/opt/cerberus-staging-current"
RELEASE_ROOT="/opt/cerberus-releases"
ROLLBACK_FILE="/var/lib/cerberus/previous-production-release"

PRODUCTION_SERVICE="cerberus.service"
STAGING_SERVICE="cerberus-staging.service"

resolve_link() {
    local link_path="$1"

    if [ -e "$link_path" ] || [ -L "$link_path" ]; then
        readlink -f "$link_path"
    else
        printf '%s\n' "MISSING"
    fi
}

service_state() {
    local service_name="$1"

    if systemctl is-active --quiet "$service_name"; then
        printf '%s\n' "active"
    else
        systemctl is-active "$service_name" 2>/dev/null || true
    fi
}

service_enabled() {
    local service_name="$1"

    systemctl is-enabled "$service_name" 2>/dev/null || true
}

http_check() {
    local url="$1"

    if curl \
        --fail \
        --silent \
        --show-error \
        --max-time 5 \
        "$url" \
        >/dev/null 2>&1
    then
        printf '%s\n' "healthy"
    else
        printf '%s\n' "unreachable"
    fi
}

PRODUCTION_RELEASE="$(resolve_link "$PRODUCTION_LINK")"
STAGING_RELEASE="$(resolve_link "$STAGING_LINK")"

echo "=== CERBERUS LOCAL HOST STATUS ==="
echo

echo "Host:"
hostname

echo
echo "Production release:"
echo "$PRODUCTION_RELEASE"

echo
echo "Staging release:"
echo "$STAGING_RELEASE"

echo
echo "Rollback release:"
if sudo test -f "$ROLLBACK_FILE"; then
    sudo cat "$ROLLBACK_FILE"
else
    echo "NOT_RECORDED"
fi

echo
echo "Production service:"
echo "state=$(service_state "$PRODUCTION_SERVICE")"
echo "enabled=$(service_enabled "$PRODUCTION_SERVICE")"
echo "http=$(http_check "http://127.0.0.1:10000/")"

echo
echo "Staging service:"
echo "state=$(service_state "$STAGING_SERVICE")"
echo "enabled=$(service_enabled "$STAGING_SERVICE")"
echo "http=$(http_check "http://127.0.0.1:10001/")"

echo
echo "Listening ports:"
ss -ltn 2>/dev/null |
    awk 'NR == 1 || /:10000|:10001/' ||
    true

echo
echo "Immutable releases:"
if [ -d "$RELEASE_ROOT" ]; then
    find "$RELEASE_ROOT" \
        -mindepth 1 \
        -maxdepth 1 \
        -type d \
        -printf '%f\n' |
        sort
else
    echo "RELEASE_ROOT_MISSING"
fi

echo
if [ "$PRODUCTION_RELEASE" = "$STAGING_RELEASE" ]; then
    echo "Lane state: production and staging are aligned"
else
    echo "Lane state: staging contains a separate candidate"
fi

echo
echo "STATUS_CHECK_COMPLETE"
