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
agent=""
runtime_source=""
credential_source=""
agent_runtime_source=""
agent_credential_source=""

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
            agent="${2:-}"
            shift 2
            ;;
        --runtime)
            runtime_source="${2:-}"
            shift 2
            ;;
        --credentials)
            credential_source="${2:-}"
            shift 2
            ;;
        --agent-runtime)
            agent_runtime_source="${2:-}"
            shift 2
            ;;
        --agent-credentials)
            agent_credential_source="${2:-}"
            shift 2
            ;;
        *)
            die "Unknown configure option: $1"
            ;;
    esac
done

require_root
validate_scope "$scope"
require_managed_tenant "$scope" "$tenant"
acquire_managed_lock "$scope"
scope_paths "$scope" "$tenant"
[ -d "$CONFIG_DIR" ] || die "Run install before configure."

if [ -n "$agent" ]; then
    validate_agent "$agent"
    grep -Fxq "$agent" "$AGENT_REGISTRY" || die "Agent is not registered; run install --agent $agent."
    [ -n "$agent_runtime_source" ] || die "--agent-runtime FILE is required."
    [ -n "$agent_credential_source" ] || die "--agent-credentials FILE is required."
    [ -z "$runtime_source" ] && [ -z "$credential_source" ] ||
        die "Configure account and agent files in separate commands."

    validate_env_file "$agent_runtime_source"
    validate_env_file "$agent_credential_source"
    agent_dir="$AGENT_CONFIG_ROOT/$agent"
    python3 "$BUNDLE_ROOT/runtime/config_validator.py" agent \
        --agent "$agent" \
        --runtime "$agent_runtime_source" \
        --credentials "$agent_credential_source" \
        --account-runtime "$RUNTIME_FILE" \
        --state-dir "$STATE_DIR"

    runtime_temp="$agent_dir/.runtime.env.new.$$"
    credential_temp="$agent_dir/.credentials.env.new.$$"
    trap 'rm -f "$runtime_temp" "$credential_temp"' EXIT
    plugin_enabled="$(read_env_value "$agent_runtime_source" CLAWROYALE_PLUGIN_ENABLED || true)"
    {
        echo "CLAWROYALE_PLUGIN_ENABLED=$plugin_enabled"
        echo "CERBERUS_RUNTIME_AGENT_ID=$agent"
        echo "CERBERUS_MEMORY_DIR=$STATE_DIR/agents/$agent/memory"
        echo "CERBERUS_TICK_URL=http://127.0.0.1:$(read_env_value "$RUNTIME_FILE" PORT)/tick"
        echo "CLAW_ROYALE_RUNTIME_ENABLED=true"
    } >"$runtime_temp"
    chown "root:$SERVICE_USER" "$runtime_temp"
    chmod 0640 "$runtime_temp"
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$agent_credential_source" "$credential_temp"
    mv -f "$runtime_temp" "$agent_dir/runtime.env"
    mv -f "$credential_temp" "$agent_dir/credentials.env"
    trap - EXIT

    note "Agent configuration installed for $agent."
    note "Credential values were not displayed."
    exit 0
fi

[ -n "$runtime_source" ] || die "--runtime FILE is required."
[ -n "$credential_source" ] || die "--credentials FILE is required."
[ -z "$agent_runtime_source" ] && [ -z "$agent_credential_source" ] ||
    die "--agent is required with agent configuration files."

validate_env_file "$runtime_source"
validate_env_file "$credential_source"
python3 "$BUNDLE_ROOT/runtime/config_validator.py" account \
    --scope "$scope" \
    --tenant "$tenant" \
    --runtime "$runtime_source" \
    --credentials "$credential_source" \
    --config-root "$TENANT_CONFIG_ROOT" \
    --state-root "$TENANT_STATE_ROOT" \
    --log-root "$TENANT_LOG_ROOT"

runtime_temp="$CONFIG_DIR/.runtime.env.new.$$"
credential_temp="$CONFIG_DIR/.credentials.env.new.$$"
trap 'rm -f "$runtime_temp" "$credential_temp"' EXIT
install -o root -g "$SERVICE_USER" -m 0640 "$runtime_source" "$runtime_temp"
install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$credential_source" "$credential_temp"
mv -f "$runtime_temp" "$RUNTIME_FILE"
mv -f "$credential_temp" "$CREDENTIAL_FILE"
trap - EXIT

initialize_admin_settings "$STATE_DIR/memory" "$SERVICE_USER"
port="$(read_env_value "$RUNTIME_FILE" PORT)"
install_rendered_systemd_unit \
    "$GATEWAY_SOCKET" \
    "$BUNDLE_ROOT/deploy/systemd/cerberus-gateway.socket.template" \
    -e "s|{{ACCOUNT_ID}}|$ACCOUNT_ID|g" \
    -e "s|{{LOOPBACK_PORT}}|$port|g" \
    -e "s|{{GATEWAY_SERVICE}}|$GATEWAY_SERVICE|g"
systemctl daemon-reload

note "Account configuration installed."
note "Credential values were not displayed."
note "Configure each registered agent, then run deploy."
