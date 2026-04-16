#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE_BIN=${CLAUDE_BIN:-~/.local/bin/claude}

CLAUDE_OPTS="--dangerously-skip-permissions --setting-sources project"
CLAUDE_OPTS_NON_INTERACTIVE="--print --verbose --output-format stream-json"

CLAUDE="$CLAUDE_BIN $CLAUDE_OPTS $CLAUDE_OPTS_NON_INTERACTIVE"
CLAUDE_WITH_USER_INPUT="$CLAUDE_BIN $CLAUDE_OPTS"

PROMPT="$(cat prompt.md)"

cd project

echo "=== SpecKit Step 0: Constitution ==="
$CLAUDE "/speckit.constitution Focus on code quality, testing, UX consistency" | tee -a ../output.json

echo "=== SpecKit Step 1: Specify ==="
$CLAUDE "/speckit.specify ${PROMPT}" | tee -a ../output.json

if [[ -n "${CLARIFY_STEP:-}" ]]; then
  echo "=== SpecKit Step 1.5: Clarify  ==="
  $CLAUDE_WITH_USER_INPUT "/speckit.clarify" | tee -a ../output.json
else
  echo "=== SpecKit Step 2.5: Checklist skipped (optional, set CHECKLIST_STEP=1 to enable) ==="
fi

echo "=== SpecKit Step 2: Plan  ==="
$CLAUDE "/speckit.plan" | tee -a ../output.json

if [[ -n "${CHECKLIST_STEP:-}" ]]; then
  echo "=== SpecKit Step 2.5: Checklist  ==="
  $CLAUDE "/speckit.checklist" | tee -a ../output.json
else
  echo "=== SpecKit Step 2.5: Checklist skipped (optional, set CHECKLIST_STEP=1 to enable) ==="
fi

echo "=== SpecKit Step 3: Tasks ==="
$CLAUDE "/speckit.tasks" | tee -a ../output.json

if [[ -n "${ANALYZE_STEP:-}" ]]; then
  echo "=== SpecKit Step 3.5: Analyze  ==="
  $CLAUDE "/speckit.analyze" | tee -a ../output.json
else
  echo "=== SpecKit Step 3.5: Analyze skipped (set ANALYZE_STEP=1 to enable) ==="
fi

echo "=== SpecKit Step 4: Implement ==="
$CLAUDE "/speckit.implement" | tee -a ../output.json
