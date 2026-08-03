#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
ROOT="$(
    cd -- "$SCRIPT_DIR/.."
    pwd
)"

destination=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --destination)
            destination="${2:-}"
            shift 2
            ;;
        *)
            echo "Unknown prepare-render option: $1" >&2
            exit 64
            ;;
    esac
done

[ -n "$destination" ] || {
    echo "--destination DIRECTORY is required." >&2
    exit 64
}
[ ! -e "$destination" ] || {
    echo "Destination already exists; choose a new path." >&2
    exit 1
}

mkdir -p "$destination"
completed=false
cleanup() {
    if [ "$completed" != "true" ]; then
        rm -rf "$destination"
    fi
}
trap cleanup EXIT

cp -a "$ROOT/payload/cerberus-core/." "$destination/"
cp -a "$ROOT/payload/plugins/clawroyale.ai/." "$destination/"
cp -a "$ROOT/runtime" "$destination/runtime"
cp "$ROOT/deploy/render/render.yaml.template" "$destination/render.yaml"

bash "$SCRIPT_DIR/security-scan.sh" "$destination"
completed=true

echo "Clean Render source prepared:"
echo "$destination"
echo
echo "Enter every sync:false value in your own Render dashboard."
echo "Do not add a credential file to this folder."
