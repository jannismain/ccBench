#!/bin/bash
set -euo pipefail
python3 skill_usage.py output.json skill_usage.json "${SKILL_USAGE_NAME:?set SKILL_USAGE_NAME in the eval env}"
