#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CLAUDE_CONFIG_DIR="$SCRIPT_DIR/.claude-runtime"
mkdir -p "$CLAUDE_CONFIG_DIR"
export CLAUDE_BIN=${CLAUDE:-~/.local/bin/claude}
CLAUDE_OPTS="--dangerously-skip-permissions --setting-sources project"
CLAUDE="$CLAUDE_BIN $CLAUDE_OPTS"

mkdir project || true
cd project

$CLAUDE plugin marketplace add JuliusBrussee/caveman
$CLAUDE plugin install --scope project caveman@caveman
$CLAUDE plugin list
