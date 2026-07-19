#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: build-release.sh REPOSITORY [GIT_REF]}"
ref="${2:-HEAD}"
release_root="/opt/cerberus-releases"
commit="$(git -C "$repo" rev-parse --verify "${ref}^{commit}")"

if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ref did not resolve to a full commit" >&2
  exit 2
fi

target="$release_root/$commit"
if [[ -e "$target" ]]; then
  test -f "$target/src/render_app.py"
  printf '%s\n' "$target"
  exit 0
fi

temporary="$release_root/.${commit}.tmp"
if [[ -e "$temporary" ]]; then
  echo "temporary release path already exists: $temporary" >&2
  exit 3
fi

install -d -m 0755 "$release_root" "$temporary"
git -C "$repo" archive --format=tar "$commit" | tar -xf - -C "$temporary"
test -f "$temporary/src/render_app.py"
printf '%s\n' "$commit" > "$temporary/RELEASE_COMMIT"
mv "$temporary" "$target"
printf '%s\n' "$target"
