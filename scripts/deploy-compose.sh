#!/usr/bin/env bash
set -euo pipefail

repository_root="$(git rev-parse --show-toplevel)"
cd "$repository_root"

if ! git diff --quiet -- || ! git diff --cached --quiet --; then
  echo "Refusing to deploy: tracked files have uncommitted changes." >&2
  exit 1
fi

if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "Refusing to deploy: the checkout has untracked files." >&2
  exit 1
fi

release="$(git rev-parse HEAD)"
if ! upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
  echo "Refusing to deploy: the current branch has no upstream branch." >&2
  exit 1
fi

if ! git merge-base --is-ancestor "$release" "$upstream"; then
  echo "Refusing to deploy: ${release} is not present in ${upstream}. Push it first." >&2
  exit 1
fi

source_repository_url="${APP_SOURCE_REPOSITORY_URL:-https://github.com/AbsurdSyssie/OpenScribe}"

case "$source_repository_url" in
  http://*|https://*) ;;
  *)
    echo "APP_SOURCE_REPOSITORY_URL must be an absolute HTTP or HTTPS URL." >&2
    exit 1
    ;;
esac

export APP_RELEASE="$release"
export APP_SOURCE_CODE_URL="${source_repository_url%/}/tree/${release}"

echo "Deploying OpenScribe release ${APP_RELEASE}"
echo "Corresponding source: ${APP_SOURCE_CODE_URL}"

exec docker compose --profile runtime up -d --build
