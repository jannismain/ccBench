#!/bin/bash
set -euo pipefail

cd project

file_list="$(mktemp)"
trap 'rm -f "$file_list"' EXIT

if git rev-parse --verify HEAD >/dev/null 2>&1; then
    {
        git diff --name-only --diff-filter=ACMR HEAD --
        git ls-files --others --exclude-standard
    } | awk 'NF' | sort -u > "$file_list"
else
    git ls-files | awk 'NF' | sort -u > "$file_list"
fi

cloc ${CLOC_EXTRA_ARGS:-} --exclude-dir=.venv --json --list-file="$file_list" > ../cloc.json
