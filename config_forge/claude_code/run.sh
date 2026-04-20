#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE=${CLAUDE:-~/.local/bin/claude}

CLAUDE_LOCAL_SETTINGS_ARG="--setting-sources project"
CLAUDE_OPTS="--print --verbose --output-format stream-json --dangerously-skip-permissions ${CLAUDE_LOCAL_SETTINGS_ARG}"

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
$CLAUDE $CLAUDE_LOCAL_SETTINGS_ARG plugins list | tee -a ../claude_code_environment.md
$CLAUDE $CLAUDE_LOCAL_SETTINGS_ARG agents list | tee -a ../claude_code_environment.md
$CLAUDE $CLAUDE_LOCAL_SETTINGS_ARG mcp list | tee -a ../claude_code_environment.md
$CLAUDE $CLAUDE_OPTS "$PROMPT" | tee -a ../output.json
