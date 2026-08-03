#!/usr/bin/env bash

BUNDLE_ROOT="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
    pwd
)"

SELF_ROOT="/opt/cerberus-client"
SELF_CONFIG_ROOT="/etc/cerberus-client"
SELF_STATE_ROOT="/var/lib/cerberus-client"
SELF_LOG_ROOT="/var/log/cerberus-client"
UNIT_BACKUP_ROOT="/var/lib/cerberus-client/unit-backups"

TENANT_RUNTIME_ROOT="/srv/cerberus-tenants"
TENANT_CONFIG_ROOT="/etc/cerberus-tenants"
TENANT_STATE_ROOT="/var/lib/cerberus-tenants"
TENANT_LOG_ROOT="/var/log/cerberus-tenants"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

note() {
    echo "$*"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "This command must run with sudo."
    fi
}

acquire_managed_lock() {
    local scope="$1"
    [ "$scope" = "managed" ] || return 0
    install -d -o root -g root -m 0755 /run/lock
    MANAGED_LOCK_FILE="/run/lock/cerberus-client-managed.lock"
    exec {MANAGED_LOCK_FD}>"$MANAGED_LOCK_FILE"
    chmod 0600 "$MANAGED_LOCK_FILE"
    flock -x "$MANAGED_LOCK_FD"
}

ensure_unit_backup_session() {
    if [ -z "${UNIT_BACKUP_SESSION:-}" ]; then
        install -d -o root -g root -m 0700 "$UNIT_BACKUP_ROOT"
        UNIT_BACKUP_SESSION="$(
            mktemp -d "$UNIT_BACKUP_ROOT/$(date -u '+%Y%m%dT%H%M%SZ').XXXXXX"
        )"
        chmod 0700 "$UNIT_BACKUP_SESSION"
    fi
}

install_systemd_unit() {
    local source="$1"
    local unit_name="$2"
    local destination temporary
    case "$unit_name" in
        ""|*[!A-Za-z0-9@_.-]*) die "Unsafe systemd unit name: $unit_name" ;;
    esac
    [ -f "$source" ] && [ ! -L "$source" ] ||
        die "Systemd unit source must be a regular file: $source"

    destination="/etc/systemd/system/$unit_name"
    if [ -L "$destination" ]; then
        die "Refusing to replace a symlinked systemd unit: $destination"
    fi
    if [ -f "$destination" ] && cmp -s "$source" "$destination"; then
        return 0
    fi
    if [ -e "$destination" ] && [ ! -f "$destination" ]; then
        die "Refusing to replace a non-file systemd unit path: $destination"
    fi
    if [ -f "$destination" ]; then
        ensure_unit_backup_session
        cp -a -- "$destination" "$UNIT_BACKUP_SESSION/$unit_name"
        note "Preserved previous systemd unit at $UNIT_BACKUP_SESSION/$unit_name"
    fi

    temporary="/etc/systemd/system/.$unit_name.new.$$.$RANDOM"
    if ! install -o root -g root -m 0644 "$source" "$temporary"; then
        rm -f -- "$temporary"
        return 1
    fi
    if ! mv -f -- "$temporary" "$destination"; then
        rm -f -- "$temporary"
        return 1
    fi
}

install_rendered_systemd_unit() {
    local unit_name="$1"
    local template="$2"
    local temporary status
    shift 2
    temporary="$(mktemp "/run/cerberus-unit-$unit_name.XXXXXX")"
    if sed "$@" "$template" >"$temporary"; then
        if install_systemd_unit "$temporary" "$unit_name"; then
            status=0
        else
            status=$?
        fi
    else
        status=$?
    fi
    rm -f -- "$temporary"
    return "$status"
}

validate_scope() {
    case "$1" in
        self-hosted|managed) ;;
        *) die "Scope must be self-hosted or managed." ;;
    esac
}

validate_tenant() {
    local tenant="$1"
    case "$tenant" in
        ""|*[!a-z0-9-]*|-*|*-) die "Tenant must use lowercase letters, numbers, and interior hyphens only." ;;
    esac
    if [ "${#tenant}" -gt 23 ]; then
        die "Tenant must be 23 characters or fewer for its isolated Linux identity."
    fi
}

validate_agent() {
    local agent="$1"
    case "$agent" in
        ""|*[!a-z0-9-]*|-*|*-) die "Agent must use lowercase letters, numbers, and interior hyphens only." ;;
    esac
    if [ "${#agent}" -gt 23 ]; then
        die "Agent must be 23 characters or fewer."
    fi
}

validate_port() {
    local port="$1"
    case "$port" in
        ""|*[!0-9]*) die "Port must be a number." ;;
    esac
    if [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
        die "Port must be between 1024 and 65535."
    fi
}

require_managed_tenant() {
    local scope="$1"
    local tenant="$2"
    if [ "$scope" = "managed" ]; then
        validate_tenant "$tenant"
    elif [ -n "$tenant" ]; then
        die "--tenant may only be used with --scope managed."
    fi
}

release_id() {
    python3 - "$BUNDLE_ROOT/bundle-manifest.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
value = str(manifest.get("releaseId", ""))
if not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit("bundle manifest has an invalid releaseId")
print(value)
PY
}

verify_bundle_hashes() {
    [ -f "$BUNDLE_ROOT/files.sha256" ] || die "files.sha256 is missing."
    [ -f "$BUNDLE_ROOT/bundle-manifest.json" ] || die "bundle-manifest.json is missing."
    python3 - "$BUNDLE_ROOT" <<'PY' || die "Bundle exact-set or hash verification failed."
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1]).resolve()
hash_path = root / "files.sha256"
manifest_path = root / "bundle-manifest.json"
expected = {}
for number, line in enumerate(hash_path.read_text(encoding="utf-8").splitlines(), 1):
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
    if not match:
        raise SystemExit(f"invalid hash row {number}")
    relative = match.group(2)
    candidate = pathlib.PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts or relative in expected:
        raise SystemExit(f"unsafe or duplicate hash path: {relative}")
    expected[relative] = match.group(1)

actual = {}
for candidate in root.rglob("*"):
    if candidate.is_symlink():
        raise SystemExit(f"symlink is forbidden: {candidate.relative_to(root)}")
    if not candidate.is_file():
        continue
    relative = candidate.relative_to(root).as_posix()
    if relative in {"files.sha256", "bundle-manifest.json"}:
        continue
    actual[relative] = hashlib.sha256(candidate.read_bytes()).hexdigest()

if set(actual) != set(expected):
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    raise SystemExit(f"bundle file set mismatch; missing={missing}; extra={extra}")
for relative, digest in actual.items():
    if digest != expected[relative]:
        raise SystemExit(f"hash mismatch: {relative}")

release_id = hashlib.sha256(hash_path.read_bytes()).hexdigest()
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("releaseId") != release_id or manifest.get("contentSha256") != release_id:
    raise SystemExit("bundle manifest release hash mismatch")
if manifest.get("fileCount") != len(actual):
    raise SystemExit("bundle manifest file count mismatch")
PY
    bash "$BUNDLE_ROOT/scripts/security-scan.sh" "$BUNDLE_ROOT" >/dev/null ||
        die "Bundle security scan failed."
}

ensure_self_service_user() {
    if ! getent group cerberus >/dev/null 2>&1; then
        groupadd --system cerberus
    fi
    if ! id cerberus >/dev/null 2>&1; then
        useradd \
            --system \
            --gid cerberus \
            --home-dir /var/lib/cerberus-client \
            --shell /usr/sbin/nologin \
            cerberus
    fi
}

ensure_tenant_service_user() {
    local tenant="$1"
    local tenant_user="cerberus-$tenant"
    local expected_home="$TENANT_STATE_ROOT/$tenant"
    if ! getent group "$tenant_user" >/dev/null 2>&1; then
        groupadd --system "$tenant_user"
    fi
    if ! id "$tenant_user" >/dev/null 2>&1; then
        useradd \
            --system \
            --gid "$tenant_user" \
            --home-dir "$expected_home" \
            --shell /usr/sbin/nologin \
            "$tenant_user"
    fi

    local group_name group_password group_gid group_members
    IFS=: read -r group_name group_password group_gid group_members < <(getent group "$tenant_user")
    [ "$group_name" = "$tenant_user" ] || die "Tenant group identity is inconsistent."
    [ -z "$group_members" ] || die "Tenant group must not contain supplementary members."

    local user_name user_password user_uid user_gid user_gecos user_home user_shell
    IFS=: read -r user_name user_password user_uid user_gid user_gecos user_home user_shell \
        < <(getent passwd "$tenant_user")
    [ "$user_name" = "$tenant_user" ] || die "Tenant user identity is inconsistent."
    [ "$user_gid" = "$group_gid" ] || die "Tenant user primary group is inconsistent."
    [ "$user_home" = "$expected_home" ] || die "Tenant user home is inconsistent."
    [ "$user_shell" = "/usr/sbin/nologin" ] || die "Tenant user shell must be /usr/sbin/nologin."
    [ "$user_uid" -lt 1000 ] || die "Tenant identity must use a system UID."

    local memberships
    memberships="$(id -nG "$tenant_user")"
    [ "$memberships" = "$tenant_user" ] ||
        die "Tenant user must not belong to supplementary groups."
}

atomic_link() {
    local target="$1"
    local link_path="$2"
    local temp_link="${link_path}.new.$$"
    ln -s "$target" "$temp_link"
    mv -Tf "$temp_link" "$link_path"
}

read_env_value() {
    local file="$1"
    local key="$2"
    [ -r "$file" ] || return 1
    awk -F= -v wanted="$key" '
        $1 == wanted {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$file"
}

scope_paths() {
    local scope="$1"
    local tenant="$2"
    if [ "$scope" = "managed" ]; then
        SERVICE_USER="cerberus-$tenant"
        CONFIG_DIR="$TENANT_CONFIG_ROOT/$tenant"
        STATE_DIR="$TENANT_STATE_ROOT/$tenant"
        LOG_DIR="$TENANT_LOG_ROOT/$tenant"
        POINTER_DIR="$TENANT_RUNTIME_ROOT/$tenant"
        CORE_SERVICE="cerberus-core@$tenant.service"
        ACCOUNT_ID="$tenant"
    else
        SERVICE_USER="cerberus"
        CONFIG_DIR="$SELF_CONFIG_ROOT"
        STATE_DIR="$SELF_STATE_ROOT"
        LOG_DIR="$SELF_LOG_ROOT"
        POINTER_DIR="$SELF_ROOT"
        CORE_SERVICE="cerberus-core.service"
        ACCOUNT_ID="self"
    fi
    RUNTIME_FILE="$CONFIG_DIR/runtime.env"
    CORE_FILE="$CONFIG_DIR/core.env"
    CREDENTIAL_FILE="$CONFIG_DIR/credentials.env"
    AGENT_CONFIG_ROOT="$CONFIG_DIR/agents"
    AGENT_REGISTRY="$CONFIG_DIR/agents.registry"
    GATEWAY_SERVICE="cerberus-gateway-$ACCOUNT_ID.service"
    GATEWAY_SOCKET="cerberus-gateway-$ACCOUNT_ID.socket"
    CURRENT_LINK="$POINTER_DIR/current"
    STAGING_LINK="$POINTER_DIR/staging"
    VENV_CURRENT_LINK="$POINTER_DIR/venv-current"
    VENV_STAGING_LINK="$POINTER_DIR/venv-staging"
    ROLLBACK_FILE="$STATE_DIR/previous-release"
}

core_bind_host() {
    local account="$1"
    python3 - "$account" <<'PY'
import hashlib
import sys

digest = hashlib.sha256(sys.argv[1].encode("utf-8")).digest()
octets = [1 + (value % 254) for value in digest[:3]]
print("127." + ".".join(str(value) for value in octets))
PY
}

agent_service_name() {
    local account="$1"
    local agent="$2"
    echo "clawroyale-$account-$agent.service"
}

registered_agents() {
    local registry="$1"
    if [ -r "$registry" ]; then
        sed -n -E '/^[a-z0-9]+([a-z0-9-]*[a-z0-9])?$/p' "$registry" | sort -u
    fi
}

health_check() {
    local runtime_file="$1"
    local port
    port="$(read_env_value "$runtime_file" PORT || true)"
    validate_port "$port"
    curl \
        --silent \
        --show-error \
        --fail \
        --max-time 3 \
        "http://127.0.0.1:$port/healthz" \
        >/dev/null
}

wait_for_health() {
    local runtime_file="$1"
    local attempt
    for attempt in $(seq 1 20); do
        if health_check "$runtime_file"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

validate_env_file() {
    local file="$1"
    [ -f "$file" ] || die "Configuration file not found: $file"
    python3 - "$file" <<'PY'
import re
import sys

path = sys.argv[1]
key = re.compile(r"^[A-Z][A-Z0-9_]*$")
with open(path, encoding="utf-8") as handle:
    for number, raw in enumerate(handle, 1):
        line = raw.rstrip("\r\n")
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path}:{number}: expected KEY=value")
        name, _ = line.split("=", 1)
        if not key.fullmatch(name):
            raise SystemExit(f"{path}:{number}: invalid variable name")
PY
}

reject_secrets_in_runtime() {
    local file="$1"
    python3 - "$file" <<'PY'
import re
import sys

sensitive = re.compile(
    r"(?:API_KEY|PRIVATE_KEY|PASSWORD|SECRET|TOKEN|MONGODB_URI|CERBERUS_PIN)$"
)
with open(sys.argv[1], encoding="utf-8") as handle:
    for number, raw in enumerate(handle, 1):
        line = raw.rstrip("\r\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _ = line.split("=", 1)
        if sensitive.search(name):
            raise SystemExit(
                f"{sys.argv[1]}:{number}: credential variable belongs in the credential file"
            )
PY
}

initialize_admin_settings() {
    local memory_dir="$1"
    local service_user="$2"
    install -d -o "$service_user" -g "$service_user" -m 0750 "$memory_dir"
    python3 - "$memory_dir/admin_settings.json" <<'PY'
import json
import os
import sys
import tempfile

destination = sys.argv[1]
payload = {}
try:
    with open(destination, encoding="utf-8") as handle:
        loaded = json.load(handle)
        if isinstance(loaded, dict):
            payload = loaded
except (FileNotFoundError, OSError, ValueError):
    pass
settings = payload.get("settings")
if not isinstance(settings, dict):
    settings = {}
settings.update(
    {
        "trust_private_network_admin": False,
        "render_env_permissions": False,
        "prefer_existing_env_secrets": True,
    }
)
payload["settings"] = settings
folder = os.path.dirname(destination)
handle, temporary = tempfile.mkstemp(prefix=".admin-settings-", dir=folder)
try:
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
        stream.write("\n")
    os.replace(temporary, destination)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
    chown "$service_user:$service_user" "$memory_dir/admin_settings.json"
    chmod 0640 "$memory_dir/admin_settings.json"
}

tick_auth_is_protected() {
    local runtime_file="$1"
    local port missing wrong
    port="$(read_env_value "$runtime_file" PORT || true)"
    validate_port "$port"
    missing="$(
        curl --silent --output /dev/null --write-out '%{http_code}' \
            --max-time 3 \
            --request POST \
            --header 'Content-Type: application/json' \
            --data '{"state":{}}' \
            "http://127.0.0.1:$port/tick" || true
    )"
    wrong="$(
        curl --silent --output /dev/null --write-out '%{http_code}' \
            --max-time 3 \
            --request POST \
            --header 'Content-Type: application/json' \
            --header 'Authorization: Bearer intentionally-wrong' \
            --data '{"state":{}}' \
            "http://127.0.0.1:$port/tick" || true
    )"
    [ "$missing" = "401" ] && [ "$wrong" = "401" ]
}
