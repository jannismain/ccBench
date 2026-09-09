#!/usr/bin/env bash
set -euo pipefail

export RTK_TELEMETRY_DISABLED=1

if ! command -v rtk >/dev/null 2>&1; then
  brew install rtk
fi

echo "Using $(rtk --version)"

mkdir -p project && cd project

if [[ -d .pi ]]; then
  rtk init --agent pi
elif [[ -d .claude ]]; then
  rtk init --agent claude
else
  rtk init
fi
echo "RTK initialized"
