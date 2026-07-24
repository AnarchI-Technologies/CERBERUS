#!/usr/bin/env bash
set -euo pipefail

commit="${1:?usage: activate-staging-release.sh FULL_COMMIT}"
release_root="/opt/cerberus-releases"
target="$release_root/$commit"
current="/opt/cerberus-staging-current"

if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]] || [[ ! -f "$target/src/render_app.py" ]]; then
  echo "validated release not found: $commit" >&2
  exit 2
fi

temporary="/opt/.cerberus-staging-current.$$.tmp"
ln -s "$target" "$temporary"
mv -Tf "$temporary" "$current"
systemctl restart cerberus-staging.service

for _ in $(seq 1 100); do
  if curl -fsS http://127.0.0.1:10001/healthz >/dev/null 2>&1; then
    printf '%s\n' "$commit"
    exit 0
  fi
  sleep 0.1
done

echo "staging release failed loopback health check" >&2
exit 4
