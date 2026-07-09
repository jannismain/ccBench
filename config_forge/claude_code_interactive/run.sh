#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE_BIN=${CLAUDE_BIN:-~/.local/bin/claude}

CLAUDE_OPTS="--dangerously-skip-permissions --setting-sources project"
CLAUDE="$CLAUDE_BIN $CLAUDE_OPTS"
PROMPT="$(cat prompt.md)"

cd project
$CLAUDE "$PROMPT" | tee -a ../output.json
