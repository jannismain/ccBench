#!/bin/bash
set -euo pipefail

cd project

file_list="$(mktemp)"
trap 'rm -f "$file_list"' EXIT

# Find the "ccbench initial state" commit to use as the baseline.
initial_commit=$(git log --grep='^ccbench initial state$' --format='%H' -1 2>/dev/null || true)

if [ -n "${initial_commit:-}" ]; then
    {
        git diff --name-only --diff-filter=ACMR "$initial_commit" --
        git ls-files --others --exclude-standard
    } | awk 'NF' | sort -u > "$file_list"
elif git rev-parse --verify HEAD >/dev/null 2>&1; then
    {
        git diff --name-only --diff-filter=ACMR HEAD --
        git ls-files --others --exclude-standard
    } | awk 'NF' | sort -u > "$file_list"
else
    git ls-files | awk 'NF' | sort -u > "$file_list"
fi

cloc ${CLOC_EXTRA_ARGS:-} --exclude-dir=.venv --json --list-file="$file_list" > ../cloc.json
