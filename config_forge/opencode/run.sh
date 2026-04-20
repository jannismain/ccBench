#!/bin/bash
set -euo pipefail

export OPENCODE=${OPENCODE_BINARY:-~/.opencode/bin/opencode}

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
$OPENCODE --version
$OPENCODE run "$PROMPT" | tee -a ../opencode_output.json
