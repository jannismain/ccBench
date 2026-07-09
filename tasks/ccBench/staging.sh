#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

REPO_URL="https://github.com/jannismain/ccBench.git"
REPO_COMMIT="3780717a73e8488df8b084a1e40e139469293d8c"

# exit early if repo was already cloned
if git -C project rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi


rm -rf tmp
git clone --no-checkout "$REPO_URL" tmp
git -C tmp checkout --detach "$REPO_COMMIT"

mkdir -p project
shopt -s dotglob nullglob
mv tmp/* project/.
shopt -u dotglob nullglob
rm -r tmp

export BROWNFIELD=1
