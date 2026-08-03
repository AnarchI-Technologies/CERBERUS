#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
# shellcheck source=../lib/common.sh
. "$SCRIPT_DIR/../lib/common.sh"

verify_bundle_hashes
echo "Bundle exact-set verification passed."
