#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project"

PROMPT="$(cat prompt.md)"

cd project

# Step 1: Fast-forward — create proposal, specs, design, and tasks in one pass
echo "=== OpenSpec Step 1: Fast Forward (create all artifacts) ==="
$CLAUDE $CLAUDE_OPTS \
  "Invoke the skill opsx:ff via the Skill tool with the following task requirements: ${PROMPT}" \
  | tee -a ../output.json

# Step 2: Apply — implement the tasks from the change
echo "=== OpenSpec Step 2: Apply (implement tasks) ==="
$CLAUDE $CLAUDE_OPTS "/opsx:apply" | tee -a ../output.json

# Step 3: Verify — check completeness and correctness
echo "=== OpenSpec Step 3: Verify ==="
$CLAUDE $CLAUDE_OPTS "/opsx:verify" | tee -a ../output.json
