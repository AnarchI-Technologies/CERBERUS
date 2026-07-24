#!/usr/bin/env bash
set -euo pipefail

commit="${1:?usage: activate-release.sh FULL_COMMIT}"
release_root="/opt/cerberus-releases"
target="$release_root/$commit"
current="/opt/cerberus-current"

if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]] || [[ ! -f "$target/src/render_app.py" ]]; then
  echo "validated release not found: $commit" >&2
  exit 2
fi

temporary="/opt/.cerberus-current.$$.tmp"
ln -s "$target" "$temporary"
mv -Tf "$temporary" "$current"
systemctl restart cerberus.service

for _ in $(seq 1 100); do
  if curl -fsS http://127.0.0.1:10000/healthz >/dev/null 2>&1; then
    printf '%s\n' "$commit"
    exit 0
  fi
  sleep 0.1
done

echo "release failed loopback health check" >&2
exit 4
