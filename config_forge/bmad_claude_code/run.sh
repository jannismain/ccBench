#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE_BIN=${CLAUDE_BIN:-~/.local/bin/claude}

CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project"
CLAUDE="$CLAUDE_BIN $CLAUDE_OPTS"
PROMPT="$(cat prompt.md)"

AUTONOMOUS_RULES="You are in autonomous mode with NO interactive user. NEVER use AskUserQuestion. When workflows say to wait for input, present a menu, or halt, skip the wait and choose the most reasonable option. Auto-confirm all checkpoints. Use English."

cd project

if [[ -n "${BROWNFIELD:-}" ]]; then
  echo "=== BMAD: Generate Project Context ==="
  $CLAUDE "/bmad-generate-project-context" | tee -a ../output.json
fi

if [[ -n "${QUICK_FLOW:-}" ]]; then
  echo "=== BMAD Quick Dev ==="
  $CLAUDE "/bmad-quick-dev ${AUTONOMOUS_RULES} Task requirements: ${PROMPT}" | tee -a ../output.json

else

  echo "=== BMAD Step 1: Create PRD ==="
  $CLAUDE "/bmad-create-prd ${AUTONOMOUS_RULES} Task requirements: ${PROMPT}" \
    | tee -a ../output.json

  echo "=== BMAD Step 2: Create Architecture ==="
  $CLAUDE "/bmad-create-architecture ${AUTONOMOUS_RULES}" \
    | tee -a ../output.json

  echo "=== BMAD Step 3: Create Epics and Stories ==="
  $CLAUDE "/bmad-create-epics-and-stories ${AUTONOMOUS_RULES}" \
    | tee -a ../output.json

  # Step 4: Implementation
  echo "=== BMAD Step 4: Implement Stories ==="
  $CLAUDE "/bmad-dev-story ${AUTONOMOUS_RULES}" | tee -a ../output.json

  # Step 5: Code Review
  echo "=== BMAD Step 5: Code Review ==="
  $CLAUDE "/bmad-code-review ${AUTONOMOUS_RULES}" | tee -a ../output.json

fi
