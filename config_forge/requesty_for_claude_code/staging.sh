#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${REQUESTY_API_KEY:-}" ]]; then
  if ! command -v op >/dev/null 2>&1; then
    echo "Error: the 1Password CLI ('op') is required to load REQUESTY_API_KEY." >&2
    exit 1
  fi

  REQUESTY_API_KEY_OP_REFERENCE="${REQUESTY_API_KEY_OP_REFERENCE:-op://Employee/Requesty/api-key-ccbench}"
  export REQUESTY_API_KEY="$(op read "$REQUESTY_API_KEY_OP_REFERENCE")"
  echo "Loaded Requesty API key from 1Password: $REQUESTY_API_KEY_OP_REFERENCE"
else
  echo "Using REQUESTY_API_KEY from the environment."
fi

if [[ -z "$REQUESTY_API_KEY" ]]; then
  echo "Error: 1Password returned an empty Requesty API key." >&2
  exit 1
fi

export ANTHROPIC_AUTH_TOKEN="$REQUESTY_API_KEY"
echo "Loaded Requesty API key from 1Password."
