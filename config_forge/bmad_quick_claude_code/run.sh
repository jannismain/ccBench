#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export CLAUDE_CONFIG_DIR="$PROJECT_ROOT/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

MODEL="${MODEL:-opus}"
CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project --model ${MODEL}"

PROMPT="$(cat prompt.md)"

AUTONOMOUS_RULES="You are in autonomous mode with NO interactive user. NEVER use AskUserQuestion. When workflows say to wait for input, present a menu, or halt, skip the wait and choose the most reasonable option. Auto-confirm all checkpoints. Use English."

cd project

echo "=== BMAD Quick Spec ==="
$CLAUDE $CLAUDE_OPTS \
  "Invoke the skill bmad-quick-spec via the Skill tool. ${AUTONOMOUS_RULES} Task requirements: ${PROMPT}" \
  | tee ../output.json

echo "=== BMAD Quick Dev ==="
$CLAUDE $CLAUDE_OPTS --continue \
  "Invoke the skill bmad-quick-dev via the Skill tool. ${AUTONOMOUS_RULES}" \
  | tee -a ../output.json
