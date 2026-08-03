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
requested_release=""
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
        --release)
            requested_release="${2:-}"
            shift 2
            ;;
        --agent)
            agents+=("${2:-}")
            shift 2
            ;;
        *)
            die "Unknown deploy option: $1"
            ;;
    esac
done

require_root
validate_scope "$scope"
require_managed_tenant "$scope" "$tenant"
acquire_managed_lock "$scope"
scope_paths "$scope" "$tenant"

[ -d "$POINTER_DIR" ] || die "Run install before deploy."
[ -r "$RUNTIME_FILE" ] || die "Account runtime configuration is missing."
[ -r "$CREDENTIAL_FILE" ] || die "Account credential configuration is missing."

if [ "${#agents[@]}" -eq 0 ]; then
    mapfile -t agents < <(registered_agents "$AGENT_REGISTRY")
fi
[ "${#agents[@]}" -gt 0 ] || die "No plugin agents are registered."
for agent in "${agents[@]}"; do
    validate_agent "$agent"
    grep -Fxq "$agent" "$AGENT_REGISTRY" || die "Agent is not registered: $agent"
    python3 "$BUNDLE_ROOT/runtime/config_validator.py" agent \
        --agent "$agent" \
        --runtime "$AGENT_CONFIG_ROOT/$agent/runtime.env" \
        --credentials "$AGENT_CONFIG_ROOT/$agent/credentials.env" \
        --account-runtime "$RUNTIME_FILE" \
        --state-dir "$STATE_DIR" \
        --installed
done

python3 "$BUNDLE_ROOT/runtime/config_validator.py" account \
    --scope "$scope" \
    --tenant "$tenant" \
    --runtime "$RUNTIME_FILE" \
    --credentials "$CREDENTIAL_FILE" \
    --config-root "$TENANT_CONFIG_ROOT" \
    --state-root "$TENANT_STATE_ROOT" \
    --log-root "$TENANT_LOG_ROOT"

initialize_admin_settings "$STATE_DIR/memory" "$SERVICE_USER"

if [ -n "$requested_release" ]; then
    case "$requested_release" in
        *[!0-9a-f]*|"") die "Release ID must be lowercase hexadecimal." ;;
    esac
    [ "${#requested_release}" -eq 64 ] || die "Release ID must contain 64 characters."
    release_path="$SELF_ROOT/releases/$requested_release"
    venv_path="$SELF_ROOT/venvs/$requested_release"
    [ -d "$release_path" ] || die "Requested release is not installed."
    [ -x "$venv_path/bin/python" ] || die "Requested release environment is not installed."
    atomic_link "$release_path" "$STAGING_LINK"
    atomic_link "$venv_path" "$VENV_STAGING_LINK"
fi

staged_release="$(readlink -f "$STAGING_LINK" 2>/dev/null || true)"
staged_venv="$(readlink -f "$VENV_STAGING_LINK" 2>/dev/null || true)"
[ -d "$staged_release" ] || die "Staged release pointer is invalid."
[ -x "$staged_venv/bin/python" ] || die "Staged release environment is invalid."

previous_release="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
previous_venv="$(readlink -f "$VENV_CURRENT_LINK" 2>/dev/null || true)"

if [ -n "$previous_release" ] && [ -d "$previous_release" ]; then
    basename "$previous_release" >"$ROLLBACK_FILE"
    chown "$SERVICE_USER:$SERVICE_USER" "$ROLLBACK_FILE"
    chmod 0640 "$ROLLBACK_FILE"
fi

rollback() {
    note "Deployment validation failed. Restoring the previous release." >&2
    if [ -n "$previous_release" ] && [ -d "$previous_release" ] &&
       [ -n "$previous_venv" ] && [ -x "$previous_venv/bin/python" ]; then
        atomic_link "$previous_release" "$CURRENT_LINK"
        atomic_link "$previous_venv" "$VENV_CURRENT_LINK"
        systemctl restart "$CORE_SERVICE" || true
        systemctl restart "$GATEWAY_SOCKET" "$GATEWAY_SERVICE" || true
        for rollback_agent in "${agents[@]}"; do
            systemctl restart "$(agent_service_name "$ACCOUNT_ID" "$rollback_agent")" || true
        done
        if wait_for_health "$RUNTIME_FILE" && tick_auth_is_protected "$RUNTIME_FILE"; then
            die "Deployment failed; the previous protected release is healthy again."
        fi
        die "Deployment and automatic rollback both failed validation."
    fi
    systemctl stop "$CORE_SERVICE" || true
    systemctl stop "$GATEWAY_SERVICE" "$GATEWAY_SOCKET" || true
    for rollback_agent in "${agents[@]}"; do
        systemctl stop "$(agent_service_name "$ACCOUNT_ID" "$rollback_agent")" || true
    done
    die "Deployment failed and no prior release was available."
}

atomic_link "$staged_release" "$CURRENT_LINK"
atomic_link "$staged_venv" "$VENV_CURRENT_LINK"
if ! systemctl restart "$CORE_SERVICE"; then
    rollback
fi
if ! systemctl restart "$GATEWAY_SOCKET"; then
    rollback
fi
if ! systemctl restart "$GATEWAY_SERVICE"; then
    rollback
fi

if ! wait_for_health "$RUNTIME_FILE"; then
    rollback
fi
if ! tick_auth_is_protected "$RUNTIME_FILE"; then
    rollback
fi

for agent in "${agents[@]}"; do
    service_name="$(agent_service_name "$ACCOUNT_ID" "$agent")"
    if ! systemctl restart "$service_name"; then
        rollback
    fi
    sleep 1
    if ! systemctl is-active "$service_name" >/dev/null 2>&1; then
        rollback
    fi
done

note "Deployment is healthy and the tick endpoint rejects missing or wrong bearer tokens."
note "release=$(basename "$staged_release")"
note "core_service=$CORE_SERVICE"
for agent in "${agents[@]}"; do
    note "agent_service=$(agent_service_name "$ACCOUNT_ID" "$agent")"
done
