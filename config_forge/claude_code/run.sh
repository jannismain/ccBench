#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export CLAUDE_CONFIG_DIR="$PROJECT_ROOT/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions --setting-sources project"

if [ -f prompt.md ]; then
    echo "Using prompt.md"
    PROMPT="$(cat prompt.md)"
elif [ -n "${AGENT_PROMPT:-}" ]; then
    echo "Using AGENT_PROMPT"
    PROMPT="$AGENT_PROMPT"
else
    echo "Using fallback prompt"
    PROMPT="What is 1 + 1?"
fi

cd project
$CLAUDE $CLAUDE_OPTS "$PROMPT" | tee ../output.json
