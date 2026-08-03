#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
# shellcheck source=../lib/common.sh
. "$SCRIPT_DIR/../lib/common.sh"

scope="self-hosted"
tenant=""
failures=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --scope)
            scope="${2:-}"
            shift 2
            ;;
        --tenant)
            tenant="${2:-}"
            shift 2
            ;;
        *)
            die "Unknown doctor option: $1"
            ;;
    esac
done

validate_scope "$scope"
require_managed_tenant "$scope" "$tenant"

pass() {
    echo "PASS  $*"
}

warn() {
    echo "INFO  $*"
}

fail() {
    echo "FAIL  $*"
    failures=$((failures + 1))
}

if [ -r /etc/os-release ] && grep -q '^ID=ubuntu' /etc/os-release; then
    pass "Ubuntu detected"
else
    fail "Ubuntu is required for the supported Linux path"
fi

if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
    pass "systemd is active"
else
    fail "systemd is not PID 1; enable it in WSL2 Ubuntu"
fi

for command in python3 sha256sum systemctl curl flock; do
    if command -v "$command" >/dev/null 2>&1; then
        pass "$command is available"
    else
        fail "$command is missing"
    fi
done

if command -v python3 >/dev/null 2>&1 &&
   python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    pass "Python 3.12 or newer is available"
else
    fail "Python 3.12 or newer is required"
fi

if verify_bundle_hashes 2>/dev/null; then
    pass "bundle hashes are valid"
else
    fail "bundle hashes are invalid"
fi

scope_paths "$scope" "$tenant"
if [ -d "$POINTER_DIR" ]; then
    pass "account deployment layout exists"
else
    warn "deployment is not installed yet"
fi

if [ -f "$CREDENTIAL_FILE" ]; then
    mode="$(stat -c '%a' "$CREDENTIAL_FILE")"
    owner="$(stat -c '%U' "$CREDENTIAL_FILE")"
    if [ "$mode" = "600" ] && [ "$owner" = "$SERVICE_USER" ]; then
        pass "account credential file is isolated"
    else
        fail "account credential owner/mode must be $SERVICE_USER/600"
    fi
else
    warn "account credential file is not installed yet"
fi

if [ "$scope" = "managed" ] && id "$SERVICE_USER" >/dev/null 2>&1; then
    pass "tenant has a dedicated OS identity"
elif [ "$scope" = "managed" ]; then
    warn "tenant OS identity is not installed yet"
fi

if [ "$scope" = "managed" ] && [ -f "$RUNTIME_FILE" ]; then
    if python3 "$BUNDLE_ROOT/runtime/config_validator.py" account \
        --scope "$scope" \
        --tenant "$tenant" \
        --runtime "$RUNTIME_FILE" \
        --credentials "$CREDENTIAL_FILE" \
        --config-root "$TENANT_CONFIG_ROOT" \
        --state-root "$TENANT_STATE_ROOT" \
        --log-root "$TENANT_LOG_ROOT" 2>/dev/null; then
        pass "Anar Core account-scope invariants are satisfied"
    else
        fail "Anar Core account-scope invariants are not satisfied"
    fi
fi

while IFS= read -r agent; do
    service_name="$(agent_service_name "$ACCOUNT_ID" "$agent")"
    credential="$AGENT_CONFIG_ROOT/$agent/credentials.env"
    if [ -f "$credential" ] &&
       [ "$(stat -c '%a' "$credential")" = "600" ] &&
       [ "$(stat -c '%U' "$credential")" = "$SERVICE_USER" ]; then
        pass "agent $agent credentials are account-isolated"
    else
        fail "agent $agent credential isolation is invalid"
    fi
    if systemctl is-active "$service_name" >/dev/null 2>&1; then
        pass "agent $agent worker is active"
    else
        warn "agent $agent worker is not active"
    fi
done < <(registered_agents "$AGENT_REGISTRY")

if systemctl is-active "$CORE_SERVICE" >/dev/null 2>&1; then
    if systemctl is-active "$GATEWAY_SERVICE" >/dev/null 2>&1 &&
       systemctl is-active "$GATEWAY_SOCKET" >/dev/null 2>&1; then
        pass "default-deny account gateway is active"
    else
        fail "account gateway service/socket is not active"
    fi
    if health_check "$RUNTIME_FILE" 2>/dev/null; then
        pass "core health endpoint is ready"
    else
        fail "core is active but health is unavailable"
    fi
    if tick_auth_is_protected "$RUNTIME_FILE" 2>/dev/null; then
        pass "loopback tick endpoint rejects missing and wrong bearer tokens"
    else
        fail "loopback tick endpoint did not enforce bearer authentication"
    fi
else
    warn "core service is not active"
fi

if [ "$failures" -gt 0 ]; then
    echo
    echo "Doctor found $failures blocking issue(s)."
    exit 1
fi

echo
echo "Doctor passed."
