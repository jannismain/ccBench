#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project --model sonnet[1m]"
CLAUDE_OPTS_INTERACTIVE="--dangerously-skip-permissions --setting-sources project --model sonnet[1m]"

PROMPT="$(cat prompt.md)"

cd project

echo "=== OpenSpec Step 1: Propose (new change) ==="
$CLAUDE $CLAUDE_OPTS "/opsx:propose ${PROMPT}" | tee -a ../output.json
git add . && git commit -am "opsx:propose" || echo "No changes to commit"

echo "=== OpenSpec Step 2: Apply (implement tasks) ==="
$CLAUDE $CLAUDE_OPTS "/opsx:apply" | tee -a ../output.json
git add . && git commit -am "opsx:apply" || echo "No changes to commit"

echo "=== OpenSpec Step 3: Verify ==="
$CLAUDE $CLAUDE_OPTS "/opsx:verify fix any major issues" | tee -a ../output.json
git add . && git commit -am "opsx:verify fix any major issues" || echo "No changes to commit"

echo "=== OpenSpec Step 4: Archive ==="
$CLAUDE $CLAUDE_OPTS "/opsx:archive" | tee -a ../output.json
git add . && git commit -am "opsx:archive" || echo "No changes to commit"
