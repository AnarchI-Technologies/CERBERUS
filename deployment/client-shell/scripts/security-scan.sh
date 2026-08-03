#!/usr/bin/env bash
set -Eeuo pipefail

root="${1:?usage: security-scan.sh DIRECTORY}"
[ -d "$root" ] || {
    echo "Scan target is not a directory: $root" >&2
    exit 1
}

bad_path="$(
    find "$root" -mindepth 1 \
        \( \
            -name '.env' -o -name '.env.*' -o \
            -name '.git' -o -name '.gitignore' -o -name '.gitattributes' -o -name '.gitmodules' -o \
            -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' -o \
            -name 'id_rsa' -o -name 'id_ed25519' -o \
            -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' -o -name '*.db3' -o \
            -type d \( -name memory -o -name memories -o -name state \) \
        \) \
        -print -quit
)"
if [ -n "$bad_path" ]; then
    echo "Forbidden file or state directory: $bad_path" >&2
    exit 1
fi

private_marker='-----''BEGIN'
if grep -I -R -n -E \
    "(BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-(proj-)?[A-Za-z0-9_-]{16,}|${private_marker})" \
    "$root" >/dev/null 2>&1; then
    echo "Recognizable credential or private-key material found." >&2
    exit 1
fi

if grep -I -R -n -E \
    '^[[:space:]]*([A-Z][A-Z0-9_]*(API_KEY|PRIVATE_KEY|PASSWORD|SECRET|TOKEN)|SECRET|TOKEN|PASSWORD)[[:space:]]*=[[:space:]]*[^#[:space:]][^[:space:]]*' \
    "$root" >/dev/null 2>&1; then
    echo "A non-empty credential assignment was found." >&2
    exit 1
fi

if grep -I -R -n -E \
    '([A-Za-z]:\\Users\\[^\\[:space:]]+|/mnt/[a-z]/Users/[^/[:space:]]+/|/home/[A-Za-z0-9._-]+/)' \
    "$root" >/dev/null 2>&1; then
    echo "A machine-specific user path was found." >&2
    exit 1
fi

echo "Security scan passed."
