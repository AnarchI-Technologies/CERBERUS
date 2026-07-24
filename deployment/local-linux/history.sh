#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_ROOT="/opt/cerberus-releases"
PRODUCTION_LINK="/opt/cerberus-current"
STAGING_LINK="/opt/cerberus-staging-current"
ROLLBACK_FILE="/var/lib/cerberus/previous-production-release"

PRODUCTION="$(readlink -f "$PRODUCTION_LINK" 2>/dev/null || true)"
STAGING="$(readlink -f "$STAGING_LINK" 2>/dev/null || true)"
ROLLBACK="$(sudo cat "$ROLLBACK_FILE" 2>/dev/null || true)"

manifest_value() {
    local manifest="$1"
    local key="$2"

    sed -nE \
        "s/^[[:space:]]*\"$key\":[[:space:]]*\"([^\"]+)\".*/\1/p" \
        "$manifest" |
        head -1
}

manifest_number() {
    local manifest="$1"
    local key="$2"

    sed -nE \
        "s/^[[:space:]]*\"$key\":[[:space:]]*([0-9]+).*/\1/p" \
        "$manifest" |
        head -1
}

lane_for_release() {
    local release="$1"
    local lane=""

    if [ "$release" = "$PRODUCTION" ]; then
        lane="production"
    fi

    if [ "$release" = "$STAGING" ]; then
        if [ -n "$lane" ]; then
            lane="$lane+staging"
        else
            lane="staging"
        fi
    fi

    if [ "$release" = "$ROLLBACK" ]; then
        if [ -n "$lane" ]; then
            lane="$lane+rollback"
        else
            lane="rollback"
        fi
    fi

    printf '%s\n' "${lane:-archived}"
}

echo "=== CERBERUS RELEASE HISTORY ==="
echo
printf '%-12s %-20s %-12s %-9s %-8s %s\n' \
    "COMMIT" \
    "BUILT" \
    "VERIFY" \
    "TESTS" \
    "SKIPPED" \
    "LANE"

printf '%-12s %-20s %-12s %-9s %-8s %s\n' \
    "------------" \
    "--------------------" \
    "------------" \
    "---------" \
    "--------" \
    "-------------------"

while IFS= read -r release; do
    manifest="$release/release.json"
    commit="$(basename "$release")"
    built="unknown"
    verification="unknown"
    tests="-"
    skipped="-"

    if [ -f "$manifest" ]; then
        built="$(manifest_value "$manifest" "built_at")"
        verification="$(manifest_value "$manifest" "verification")"
        tests="$(manifest_number "$manifest" "run")"
        skipped="$(manifest_number "$manifest" "skipped")"

        built="${built:-unknown}"
        verification="${verification:-unknown}"
        tests="${tests:--}"
        skipped="${skipped:--}"
    fi

    lane="$(lane_for_release "$release")"

    printf '%-12s %-20s %-12s %-9s %-8s %s\n' \
        "${commit:0:12}" \
        "${built:0:20}" \
        "$verification" \
        "$tests" \
        "$skipped" \
        "$lane"
done < <(
    find "$RELEASE_ROOT" \
        -mindepth 1 \
        -maxdepth 1 \
        -type d \
        -printf '%T@ %p\n' |
        sort -nr |
        awk '{ print $2 }'
)

echo
echo "RELEASE_HISTORY_COMPLETE"
