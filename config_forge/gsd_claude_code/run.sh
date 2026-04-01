#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project"

PROMPT="$(cat prompt.md)"

cd project

# Step 0:
if [[ -n "${BROWNFIELD:-}" ]]; then
  echo "=== GSD Step 0: Initialize for existing project ==="
  $CLAUDE $CLAUDE_OPTS "/gsd:map-codebase" | tee -a ../output.json
fi

echo "=== GSD Step 1: Input ==="
$CLAUDE $CLAUDE_OPTS \
  "Invoke the skill gsd:new-project via the Skill tool to prepare the following change request: $PROMPT" \
  | tee -a ../output.json

echo "=== GSD Step 2: Implement ==="
$CLAUDE $CLAUDE_OPTS \
  "Invoke the skill gsd:autonomous via the Skill tool to implement the current change request." \
  | tee -a ../output.json
