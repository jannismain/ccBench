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

# Step 1: PRD (starts a new session; --continue on subsequent steps resumes it)
echo "=== BMAD Step 1: Create PRD ==="
$CLAUDE $CLAUDE_OPTS \
  "Invoke the skill bmad-create-prd via the Skill tool. ${AUTONOMOUS_RULES} Task requirements: ${PROMPT}" \
  | tee ../output.json

# Step 2: Architecture
echo "=== BMAD Step 2: Create Architecture ==="
$CLAUDE $CLAUDE_OPTS --continue \
  "Invoke the skill bmad-create-architecture via the Skill tool. ${AUTONOMOUS_RULES}" \
  | tee -a ../output.json

# Step 3: Epics and Stories
echo "=== BMAD Step 3: Create Epics and Stories ==="
$CLAUDE $CLAUDE_OPTS --continue \
  "Invoke the skill bmad-create-epics-and-stories via the Skill tool. ${AUTONOMOUS_RULES}" \
  | tee -a ../output.json

# Step 4: Implementation
echo "=== BMAD Step 4: Implement Stories ==="
$CLAUDE $CLAUDE_OPTS --continue \
  "Invoke the skill bmad-dev-story via the Skill tool for each story from the epics. Implement stories sequentially. ${AUTONOMOUS_RULES}" \
  | tee -a ../output.json

# Step 5: Code Review
echo "=== BMAD Step 5: Code Review ==="
$CLAUDE $CLAUDE_OPTS --continue \
  "Invoke the skill bmad-code-review via the Skill tool. ${AUTONOMOUS_RULES}" \
  | tee -a ../output.json
