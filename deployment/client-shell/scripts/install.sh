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
port="10000"
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
        --port)
            port="${2:-}"
            shift 2
            ;;
        --agent)
            agents+=("${2:-}")
            shift 2
            ;;
        *)
            die "Unknown install option: $1"
            ;;
    esac
done

if [ "${#agents[@]}" -eq 0 ]; then
    agents=("default")
fi
for agent in "${agents[@]}"; do
    validate_agent "$agent"
done

require_root
validate_scope "$scope"
require_managed_tenant "$scope" "$tenant"
acquire_managed_lock "$scope"
validate_port "$port"
verify_bundle_hashes

if [ "$scope" = "managed" ]; then
    while IFS= read -r existing; do
        [ "$existing" = "$TENANT_CONFIG_ROOT/$tenant/runtime.env" ] && continue
        if [ "$(read_env_value "$existing" PORT || true)" = "$port" ]; then
            die "Managed port $port is already assigned to another tenant."
        fi
    done < <(find "$TENANT_CONFIG_ROOT" -mindepth 2 -maxdepth 2 -name runtime.env -type f 2>/dev/null)
    ensure_tenant_service_user "$tenant"
else
    ensure_self_service_user
fi
scope_paths "$scope" "$tenant"

release="$(release_id)"
release_root="$SELF_ROOT/releases"
venv_root="$SELF_ROOT/venvs"
release_path="$release_root/$release"
venv_path="$venv_root/$release"

install -d -o root -g root -m 0755 "$SELF_ROOT" "$release_root" "$venv_root"

if [ ! -d "$release_path" ]; then
    temp_release="$release_root/.installing-$release-$$"
    trap 'rm -rf "$temp_release"' EXIT
    install -d -o root -g root -m 0755 "$temp_release"
    cp -a "$BUNDLE_ROOT/payload/cerberus-core/." "$temp_release/"
    cp -a "$BUNDLE_ROOT/payload/plugins/clawroyale.ai/." "$temp_release/"
    cp -a "$BUNDLE_ROOT/runtime" "$temp_release/runtime"
    [ -f "$temp_release/requirements.txt" ] || die "Release is missing requirements.txt."
    [ -f "$temp_release/src/render_app.py" ] || die "Release is missing src/render_app.py."
    [ -f "$temp_release/src/claw_runtime.py" ] || die "Release is missing the plugin worker."
    [ -f "$temp_release/runtime/claw_worker_launcher.py" ] || die "Release is missing the worker launcher."
    chown -R root:root "$temp_release"
    find "$temp_release" -type d -exec chmod 0755 {} +
    find "$temp_release" -type f -exec chmod 0644 {} +
    mv "$temp_release" "$release_path"
    trap - EXIT
fi

if [ ! -f "$venv_path/.complete" ]; then
    [ ! -L "$venv_path" ] || die "Release environment cannot be a symlink."
    if [ -e "$venv_path" ]; then
        incomplete_backup="${venv_path}.incomplete.$(date -u '+%Y%m%d%H%M%S')"
        mv "$venv_path" "$incomplete_backup"
        note "Preserved incomplete release environment at $incomplete_backup"
    fi
    temp_venv="$venv_root/.installing-$release-$$"
    trap 'rm -rf "$temp_venv"' EXIT
    python3 -m venv "$temp_venv"
    "$temp_venv/bin/python" -m pip install \
        --disable-pip-version-check \
        --upgrade pip
    "$temp_venv/bin/python" -m pip install \
        --disable-pip-version-check \
        --requirement "$release_path/requirements.txt"
    PYTHONPYCACHEPREFIX="$temp_venv/pycache" \
        "$temp_venv/bin/python" -m compileall -q "$release_path/src" "$release_path/data"
    PYTHONDONTWRITEBYTECODE=1 "$temp_venv/bin/python" -B - "$release_path" <<'PY'
import importlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
sys.path[:0] = [str(root / "src"), str(root / "data"), str(root)]
for module in ("render_app", "claw_runtime"):
    importlib.import_module(module)
PY
    touch "$temp_venv/.complete"
    chown -R root:root "$temp_venv"
    mv "$temp_venv" "$venv_path"
    trap - EXIT
fi

if [ "$scope" = "managed" ]; then
    core_unit="cerberus-core@.service"
else
    core_unit="cerberus-core.service"
fi
install_systemd_unit \
    "$BUNDLE_ROOT/deploy/systemd/$core_unit" \
    "$core_unit"

if [ "$scope" = "managed" ]; then
    service_user="cerberus-$tenant"
    config_dir="$TENANT_CONFIG_ROOT/$tenant"
    state_dir="$TENANT_STATE_ROOT/$tenant"
    log_dir="$TENANT_LOG_ROOT/$tenant"
    pointer_dir="$TENANT_RUNTIME_ROOT/$tenant"

    install -d -o root -g root -m 0711 \
        "$TENANT_CONFIG_ROOT" "$TENANT_STATE_ROOT" "$TENANT_LOG_ROOT" "$TENANT_RUNTIME_ROOT"
    install -d -o root -g "$service_user" -m 0750 "$config_dir"
    install -d -o "$service_user" -g "$service_user" -m 0750 \
        "$state_dir" "$state_dir/state" "$state_dir/memory" "$state_dir/agents" "$log_dir"
    install -d -o root -g root -m 0755 "$pointer_dir"

    if [ ! -f "$config_dir/runtime.env" ]; then
        sed \
            -e "s|^PORT=.*|PORT=$port|" \
            -e "s|^CERBERUS_MEMORY_DIR=.*|CERBERUS_MEMORY_DIR=$state_dir/memory|" \
            -e "s|^CERBERUS_STATE_DIR=.*|CERBERUS_STATE_DIR=$state_dir/state|" \
            -e "s|^CERBERUS_LOG_DIR=.*|CERBERUS_LOG_DIR=$log_dir|" \
            "$BUNDLE_ROOT/config/runtime-managed-tenant.template" \
            >"$config_dir/runtime.env"
        chown "root:$service_user" "$config_dir/runtime.env"
        chmod 0640 "$config_dir/runtime.env"
    fi
    if [ ! -f "$config_dir/credentials.env" ]; then
        install \
            -o "$service_user" -g "$service_user" -m 0600 \
            "$BUNDLE_ROOT/config/credentials.template" \
            "$config_dir/credentials.env"
    fi

    initialize_admin_settings "$state_dir/memory" "$service_user"
    atomic_link "$release_path" "$pointer_dir/staging"
    atomic_link "$venv_path" "$pointer_dir/venv-staging"
else
    service_user="cerberus"
    config_dir="$SELF_CONFIG_ROOT"
    state_dir="$SELF_STATE_ROOT"
    log_dir="$SELF_LOG_ROOT"
    pointer_dir="$SELF_ROOT"

    install -d -o root -g "$service_user" -m 0750 "$config_dir"
    install -d -o "$service_user" -g "$service_user" -m 0750 \
        "$state_dir" "$state_dir/state" "$state_dir/memory" "$state_dir/agents" "$log_dir"

    if [ ! -f "$config_dir/runtime.env" ]; then
        install \
            -o root -g "$service_user" -m 0640 \
            "$BUNDLE_ROOT/config/runtime-self-hosted.template" \
            "$config_dir/runtime.env"
    fi
    if [ ! -f "$config_dir/credentials.env" ]; then
        install \
            -o "$service_user" -g "$service_user" -m 0600 \
            "$BUNDLE_ROOT/config/credentials.template" \
            "$config_dir/credentials.env"
    fi

    initialize_admin_settings "$state_dir/memory" "$service_user"
    atomic_link "$release_path" "$pointer_dir/staging"
    atomic_link "$venv_path" "$pointer_dir/venv-staging"
fi

core_host="$(core_bind_host "$ACCOUNT_ID")"
if [ "$scope" = "managed" ]; then
    while IFS= read -r existing_core; do
        [ "$existing_core" = "$config_dir/core.env" ] && continue
        if [ "$(read_env_value "$existing_core" CERBERUS_BIND_HOST || true)" = "$core_host" ]; then
            die "Derived core loopback address collides with another tenant."
        fi
    done < <(find "$TENANT_CONFIG_ROOT" -mindepth 2 -maxdepth 2 -name core.env -type f 2>/dev/null)
fi
{
    echo "PORT=10000"
    echo "CERBERUS_BIND_HOST=$core_host"
    echo "CLAW_ROYALE_RUNTIME_ENABLED=false"
} >"$config_dir/core.env"
chown "root:$service_user" "$config_dir/core.env"
chmod 0640 "$config_dir/core.env"

install_rendered_systemd_unit \
    "$GATEWAY_SERVICE" \
    "$BUNDLE_ROOT/deploy/systemd/cerberus-gateway.service.template" \
    -e "s|{{ACCOUNT_ID}}|$ACCOUNT_ID|g" \
    -e "s|{{SERVICE_USER}}|$service_user|g" \
    -e "s|{{CORE_SERVICE}}|$CORE_SERVICE|g" \
    -e "s|{{GATEWAY_SOCKET}}|$GATEWAY_SOCKET|g" \
    -e "s|{{CURRENT_LINK}}|$pointer_dir/current|g" \
    -e "s|{{VENV_LINK}}|$pointer_dir/venv-current|g" \
    -e "s|{{ACCOUNT_CREDENTIALS}}|$config_dir/credentials.env|g" \
    -e "s|{{CORE_BIND_HOST}}|$core_host|g"

install_rendered_systemd_unit \
    "$GATEWAY_SOCKET" \
    "$BUNDLE_ROOT/deploy/systemd/cerberus-gateway.socket.template" \
    -e "s|{{ACCOUNT_ID}}|$ACCOUNT_ID|g" \
    -e "s|{{LOOPBACK_PORT}}|$port|g" \
    -e "s|{{GATEWAY_SERVICE}}|$GATEWAY_SERVICE|g"

agent_config_root="$config_dir/agents"
install -d -o root -g "$service_user" -m 0750 "$agent_config_root"
touch "$config_dir/agents.registry"
chown "root:$service_user" "$config_dir/agents.registry"
chmod 0640 "$config_dir/agents.registry"

for agent in "${agents[@]}"; do
    agent_config="$agent_config_root/$agent"
    agent_state="$state_dir/agents/$agent"
    agent_memory="$agent_state/memory"
    agent_log="$log_dir/clawroyale-$agent.log"
    service_name="$(agent_service_name "$ACCOUNT_ID" "$agent")"

    install -d -o root -g "$service_user" -m 0750 "$agent_config"
    install -d -o "$service_user" -g "$service_user" -m 0750 "$agent_state" "$agent_memory"

    if [ ! -f "$agent_config/runtime.env" ]; then
        {
            echo "CLAWROYALE_PLUGIN_ENABLED=false"
            echo "CERBERUS_RUNTIME_AGENT_ID=$agent"
            echo "CERBERUS_MEMORY_DIR=$agent_memory"
            echo "CERBERUS_TICK_URL=http://127.0.0.1:$port/tick"
            echo "CLAW_ROYALE_RUNTIME_ENABLED=true"
        } >"$agent_config/runtime.env"
        chown "root:$service_user" "$agent_config/runtime.env"
        chmod 0640 "$agent_config/runtime.env"
    fi
    if [ ! -f "$agent_config/credentials.env" ]; then
        install \
            -o "$service_user" -g "$service_user" -m 0600 \
            "$BUNDLE_ROOT/config/agent-credentials.template" \
            "$agent_config/credentials.env"
    fi

    install_rendered_systemd_unit \
        "$service_name" \
        "$BUNDLE_ROOT/deploy/systemd/clawroyale-agent.service.template" \
        -e "s|{{ACCOUNT_ID}}|$ACCOUNT_ID|g" \
        -e "s|{{AGENT_ID}}|$agent|g" \
        -e "s|{{SERVICE_USER}}|$service_user|g" \
        -e "s|{{CORE_SERVICE}}|$CORE_SERVICE|g" \
        -e "s|{{GATEWAY_SERVICE}}|$GATEWAY_SERVICE|g" \
        -e "s|{{CURRENT_LINK}}|$pointer_dir/current|g" \
        -e "s|{{VENV_LINK}}|$pointer_dir/venv-current|g" \
        -e "s|{{ACCOUNT_RUNTIME}}|$config_dir/runtime.env|g" \
        -e "s|{{ACCOUNT_CREDENTIALS}}|$config_dir/credentials.env|g" \
        -e "s|{{AGENT_RUNTIME}}|$agent_config/runtime.env|g" \
        -e "s|{{AGENT_CREDENTIALS}}|$agent_config/credentials.env|g" \
        -e "s|{{AGENT_LOG}}|$agent_log|g" \
        -e "s|{{ACCOUNT_STATE}}|$state_dir|g" \
        -e "s|{{ACCOUNT_LOG}}|$log_dir|g"

    if ! grep -Fxq "$agent" "$config_dir/agents.registry"; then
        echo "$agent" >>"$config_dir/agents.registry"
    fi
done

sort -u -o "$config_dir/agents.registry" "$config_dir/agents.registry"
systemctl daemon-reload
systemctl enable "$CORE_SERVICE" "$GATEWAY_SOCKET" "$GATEWAY_SERVICE" >/dev/null
for agent in "${agents[@]}"; do
    systemctl enable "$(agent_service_name "$ACCOUNT_ID" "$agent")" >/dev/null
done

note "Installed immutable release:"
note "$release"
note "Registered agents:"
registered_agents "$config_dir/agents.registry" | sed 's/^/  - /'
note
note "The staged release is ready. Apply account and agent configuration, then run deploy."
