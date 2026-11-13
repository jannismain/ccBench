#!/bin/bash
set -euo pipefail
uv run --quiet --with playwright python check_prototype.py
