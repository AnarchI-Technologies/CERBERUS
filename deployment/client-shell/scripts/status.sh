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
agents=()

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
        --agent)
            agents+=("${2:-}")
            shift 2
            ;;
        *)
            die "Unknown status option: $1"
            ;;
    esac
done

validate_scope "$scope"
require_managed_tenant "$scope" "$tenant"
scope_paths "$scope" "$tenant"
if [ "${#agents[@]}" -eq 0 ]; then
    mapfile -t agents < <(registered_agents "$AGENT_REGISTRY")
fi

echo "scope=$scope"
if [ "$scope" = "managed" ]; then
    echo "tenant=$tenant"
fi
echo "account_os_user=$SERVICE_USER"
echo "core_service=$CORE_SERVICE"
echo "core_state=$(systemctl is-active "$CORE_SERVICE" 2>/dev/null || true)"
echo "gateway_service=$GATEWAY_SERVICE"
echo "gateway_state=$(systemctl is-active "$GATEWAY_SERVICE" 2>/dev/null || true)"
echo "gateway_socket_state=$(systemctl is-active "$GATEWAY_SOCKET" 2>/dev/null || true)"

current="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
staging="$(readlink -f "$STAGING_LINK" 2>/dev/null || true)"
rollback="$(cat "$ROLLBACK_FILE" 2>/dev/null || true)"
echo "current_release=${current:+$(basename "$current")}"
echo "staging_release=${staging:+$(basename "$staging")}"
echo "rollback_release=$rollback"

port="$(read_env_value "$RUNTIME_FILE" PORT 2>/dev/null || true)"
echo "loopback_port=$port"
if health_check "$RUNTIME_FILE" 2>/dev/null; then
    echo "core_health=healthy"
else
    echo "core_health=unavailable"
fi
if tick_auth_is_protected "$RUNTIME_FILE" 2>/dev/null; then
    echo "tick_auth=protected"
else
    echo "tick_auth=not-verified"
fi

if [ -r "$CREDENTIAL_FILE" ]; then
    echo "account_credentials=installed"
    echo "credential_values=hidden"
else
    echo "account_credentials=missing"
fi

for agent in "${agents[@]}"; do
    validate_agent "$agent"
    service_name="$(agent_service_name "$ACCOUNT_ID" "$agent")"
    agent_runtime="$AGENT_CONFIG_ROOT/$agent/runtime.env"
    enabled="$(read_env_value "$agent_runtime" CLAWROYALE_PLUGIN_ENABLED 2>/dev/null || true)"
    echo "agent.$agent.service=$service_name"
    echo "agent.$agent.state=$(systemctl is-active "$service_name" 2>/dev/null || true)"
    echo "agent.$agent.enabled=$enabled"
    if [ -r "$AGENT_CONFIG_ROOT/$agent/credentials.env" ]; then
        echo "agent.$agent.credentials=installed"
    else
        echo "agent.$agent.credentials=missing"
    fi
done
