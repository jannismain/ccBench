#!/bin/bash
set -euo pipefail

cd project

candidate_files="$(mktemp)"
file_list="$(mktemp)"
trap 'rm -f "$candidate_files" "$file_list"' EXIT

# Find the "ccbench initial state" commit to use as the baseline.
initial_commit=$(git log --grep='^ccbench initial state$' --format='%H' -1 2>/dev/null || true)

if [ -n "${initial_commit:-}" ]; then
    {
        git diff --name-only --diff-filter=ACMR "$initial_commit" --
        git ls-files --others --exclude-standard
    } | awk 'NF' | sort -u > "$candidate_files"
elif git rev-parse --verify HEAD >/dev/null 2>&1; then
    {
        git diff --name-only --diff-filter=ACMR HEAD --
        git ls-files --others --exclude-standard
    } | awk 'NF' | sort -u > "$candidate_files"
else
    git ls-files | awk 'NF' | sort -u > "$candidate_files"
fi

# Count authored source, not generated build output, caches, or dependency metadata.
awk -F/ '
    BEGIN {
        split(".cache .mypy_cache .next .nuxt .pytest_cache .ruff_cache .svelte-kit .turbo .venv .vite __pycache__ build coverage dist node_modules out target venv", dirs, " ")
        for (i in dirs) ignored_dirs[dirs[i]] = 1

        split("Cargo.lock Gemfile.lock Pipfile.lock bun.lock bun.lockb composer.lock npm-shrinkwrap.json package-lock.json pnpm-lock.yaml poetry.lock uv.lock yarn.lock", files, " ")
        for (i in files) ignored_files[files[i]] = 1
    }
    {
        for (i = 1; i <= NF; i++) {
            if ($i in ignored_dirs) next
        }
        if ($NF in ignored_files) next
        print
    }
' "$candidate_files" > "$file_list"

cloc ${CLOC_EXTRA_ARGS:-} --exclude-dir=.venv --list-file="$file_list"
cloc ${CLOC_EXTRA_ARGS:-} --exclude-dir=.venv --json --list-file="$file_list" > ../cloc.json

# Contribute metrics to `ccbench compare`.
mkdir -p ../metrics
python3 - ../cloc.json ../metrics/cloc.json <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text().strip()
summary = (json.loads(raw) if raw else {}).get("SUM", {})
metrics = {
    "code": {"value": summary.get("code"), "label": "Lines of code", "format": "{:.0f}", "change": "percent"},
    "files": {"value": summary.get("nFiles"), "label": "Code files", "format": "{:.0f}"},
}
Path(sys.argv[2]).write_text(json.dumps({"eval": "cloc", "metrics": metrics}, indent=2))
PY
