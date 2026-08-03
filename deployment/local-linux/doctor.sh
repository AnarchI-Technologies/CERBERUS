#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCTION_LINK="/opt/cerberus-current"
STAGING_LINK="/opt/cerberus-staging-current"
RELEASE_ROOT="/opt/cerberus-releases"
ROLLBACK_FILE="/var/lib/cerberus/previous-production-release"
PERSISTENT_ROOT="/var/data/.cerberus"
PYTHON="/opt/cerberus-venv/bin/python"

PRODUCTION_SERVICE="cerberus.service"
STAGING_SERVICE="cerberus-staging.service"

PRODUCTION_URL="http://127.0.0.1:10000/"
STAGING_URL="http://127.0.0.1:10001/"

FAILURES=0
WARNINGS=0
PASSES=0

pass() {
    PASSES=$((PASSES + 1))
    printf 'PASS  %s\n' "$1"
}

warn() {
    WARNINGS=$((WARNINGS + 1))
    printf 'WARN  %s\n' "$1"
}

fail() {
    FAILURES=$((FAILURES + 1))
    printf 'FAIL  %s\n' "$1"
}

section() {
    echo
    echo "=== $1 ==="
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

check_command() {
    local command_name="$1"

    if command_exists "$command_name"; then
        pass "Command available: $command_name"
    else
        fail "Command missing: $command_name"
    fi
}

resolved_link() {
    readlink -f "$1" 2>/dev/null || true
}

check_service() {
    local service_name="$1"

    if ! systemctl list-unit-files "$service_name" --no-legend 2>/dev/null |
        grep -q "^$service_name"
    then
        fail "Service unit missing: $service_name"
        return
    fi

    pass "Service unit exists: $service_name"

    if systemctl is-enabled --quiet "$service_name"; then
        pass "Service enabled: $service_name"
    else
        warn "Service not enabled: $service_name"
    fi

    if systemctl is-active --quiet "$service_name"; then
        pass "Service active: $service_name"
    else
        fail "Service inactive: $service_name"
    fi
}

check_http() {
    local name="$1"
    local url="$2"

    if curl \
        --fail \
        --silent \
        --show-error \
        --max-time 8 \
        "$url" \
        >/dev/null
    then
        pass "$name HTTP healthy: $url"
    else
        fail "$name HTTP unhealthy: $url"
    fi
}

check_port() {
    local port="$1"
    local name="$2"

    if ss -ltn 2>/dev/null | grep -Eq "127\.0\.0\.1:$port[[:space:]]"; then
        pass "$name listening on 127.0.0.1:$port"
    else
        fail "$name not listening on 127.0.0.1:$port"
    fi
}

check_release() {
    local lane="$1"
    local link_path="$2"
    local release

    release="$(resolved_link "$link_path")"

    if [ -z "$release" ]; then
        fail "$lane release link cannot be resolved: $link_path"
        return
    fi

    if [ ! -d "$release" ]; then
        fail "$lane release directory missing: $release"
        return
    fi

    pass "$lane release exists: $release"

    for required in \
        requirements.txt \
        src/render_app.py \
        src/claw_runtime.py
    do
        if [ -f "$release/$required" ]; then
            pass "$lane required file: $required"
        else
            fail "$lane missing required file: $required"
        fi
    done

    if [ -f "$release/release.json" ]; then
        pass "$lane manifest exists"

        if grep -Eq '"verification"[[:space:]]*:[[:space:]]*"passed"' \
            "$release/release.json"
        then
            pass "$lane manifest verification passed"
        else
            warn "$lane manifest is not marked verification=passed"
        fi
    else
        warn "$lane release predates release.json manifests"
    fi
}

section "HOST"

echo "hostname=$(hostname)"
echo "kernel=$(uname -srmo)"

if [ -r /etc/os-release ]; then
    . /etc/os-release
    echo "os=${PRETTY_NAME:-unknown}"
    pass "Ubuntu operating-system metadata readable"
else
    fail "/etc/os-release is unavailable"
fi

if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
    pass "systemd is PID 1"
else
    fail "systemd is not PID 1"
fi

section "TOOLS"

check_command bash
check_command git
check_command curl
check_command systemctl
check_command journalctl
check_command ss
check_command find
check_command tar

if [ -x "$PYTHON" ]; then
    pass "CERBERUS Python exists: $PYTHON"
    echo "python=$("$PYTHON" --version 2>&1)"
else
    fail "CERBERUS Python missing: $PYTHON"
fi

section "RESOURCES"

DISK_USED="$(
    df -P /opt 2>/dev/null |
        awk 'NR == 2 { gsub("%", "", $5); print $5 }'
)"

if [ -n "$DISK_USED" ]; then
    echo "opt_disk_used_percent=$DISK_USED"

    if [ "$DISK_USED" -ge 95 ]; then
        fail "/opt disk usage is critical: ${DISK_USED}%"
    elif [ "$DISK_USED" -ge 85 ]; then
        warn "/opt disk usage is elevated: ${DISK_USED}%"
    else
        pass "/opt disk usage is healthy: ${DISK_USED}%"
    fi
else
    fail "Unable to measure /opt disk usage"
fi

MEM_AVAILABLE="$(
    awk '/MemAvailable:/ { print $2 }' /proc/meminfo 2>/dev/null || true
)"

if [ -n "$MEM_AVAILABLE" ]; then
    echo "memory_available_kb=$MEM_AVAILABLE"

    if [ "$MEM_AVAILABLE" -lt 262144 ]; then
        fail "Available memory below 256 MiB"
    elif [ "$MEM_AVAILABLE" -lt 1048576 ]; then
        warn "Available memory below 1 GiB"
    else
        pass "Available memory is healthy"
    fi
else
    fail "Unable to read available memory"
fi

SWAP_TOTAL="$(
    awk '/SwapTotal:/ { print $2 }' /proc/meminfo 2>/dev/null || true
)"

if [ "${SWAP_TOTAL:-0}" -gt 0 ]; then
    pass "Swap is configured"
else
    warn "No swap is configured"
fi

section "SERVICES"

check_service "$PRODUCTION_SERVICE"
check_service "$STAGING_SERVICE"

section "PORTS AND HTTP"

check_port 10000 "Production"
check_port 10001 "Staging"
check_http "Production" "$PRODUCTION_URL"
check_http "Staging" "$STAGING_URL"

section "RELEASES"

if [ -d "$RELEASE_ROOT" ]; then
    pass "Release root exists: $RELEASE_ROOT"

    RELEASE_COUNT="$(
        find "$RELEASE_ROOT" \
            -mindepth 1 \
            -maxdepth 1 \
            -type d |
            awk 'END { print NR + 0 }'
    )"

    echo "release_count=$RELEASE_COUNT"

    if [ "$RELEASE_COUNT" -gt 50 ]; then
        warn "More than 50 immutable releases are installed"
    else
        pass "Immutable release count is within current threshold"
    fi
else
    fail "Release root missing: $RELEASE_ROOT"
fi

check_release "Production" "$PRODUCTION_LINK"
check_release "Staging" "$STAGING_LINK"

PRODUCTION_RELEASE="$(resolved_link "$PRODUCTION_LINK")"
STAGING_RELEASE="$(resolved_link "$STAGING_LINK")"

if [ -n "$PRODUCTION_RELEASE" ] &&
   [ "$PRODUCTION_RELEASE" = "$STAGING_RELEASE" ]
then
    pass "Production and staging are aligned"
else
    warn "Production and staging point to different releases"
fi

section "ROLLBACK"

if sudo test -f "$ROLLBACK_FILE"; then
    ROLLBACK_RELEASE="$(sudo cat "$ROLLBACK_FILE" | tr -d '\r\n')"

    if [ -n "$ROLLBACK_RELEASE" ] && [ -d "$ROLLBACK_RELEASE" ]; then
        pass "Rollback target exists: $ROLLBACK_RELEASE"
    else
        fail "Rollback target is invalid: ${ROLLBACK_RELEASE:-EMPTY}"
    fi
else
    fail "Rollback record missing: $ROLLBACK_FILE"
fi

section "PERSISTENT STORAGE"

if sudo test -d "$PERSISTENT_ROOT"; then
    pass "Persistent runtime directory exists: $PERSISTENT_ROOT"

    if sudo test -r "$PERSISTENT_ROOT"; then
        pass "Persistent runtime directory is readable"
    else
        fail "Persistent runtime directory is not readable"
    fi

    if sudo test -w "$PERSISTENT_ROOT"; then
        pass "Persistent runtime directory is writable"
    else
        fail "Persistent runtime directory is not writable"
    fi
else
    fail "Persistent runtime directory missing: $PERSISTENT_ROOT"
fi

section "TIMERS"

mapfile -t CERBERUS_TIMERS < <(
    systemctl list-unit-files \
        'cerberus*.timer' \
        --no-legend \
        2>/dev/null |
        awk '{ print $1 }'
)

if [ "${#CERBERUS_TIMERS[@]}" -eq 0 ]; then
    warn "No CERBERUS timers discovered"
else
    for timer in "${CERBERUS_TIMERS[@]}"; do
        if systemctl is-enabled --quiet "$timer"; then
            pass "Timer enabled: $timer"
        else
            warn "Timer not enabled: $timer"
        fi

        if systemctl is-active --quiet "$timer"; then
            pass "Timer active: $timer"
        else
            warn "Timer not active: $timer"
        fi
    done
fi

section "RECENT JOURNAL"

CRITICAL_LOGS="$(
    sudo journalctl \
        -u "$PRODUCTION_SERVICE" \
        -u "$STAGING_SERVICE" \
        --since "24 hours ago" \
        --no-pager 2>/dev/null |
        grep -Ei \
            'traceback|unhandled exception|fatal|critical|segmentation fault|address already in use|worker.*failed' \
        || true
)"

if [ -n "$CRITICAL_LOGS" ]; then
    warn "Potential critical markers found in the last 24 hours"
    printf '%s\n' "$CRITICAL_LOGS" | tail -20
else
    pass "No critical journal markers found in the last 24 hours"
fi

section "SUMMARY"

echo "passes=$PASSES"
echo "warnings=$WARNINGS"
echo "failures=$FAILURES"

if [ "$FAILURES" -gt 0 ]; then
    echo
    echo "DOCTOR_FAILED"
    exit 1
fi

if [ "$WARNINGS" -gt 0 ]; then
    echo
    echo "DOCTOR_PASSED_WITH_WARNINGS"
    exit 0
fi

echo
echo "DOCTOR_PASSED"
