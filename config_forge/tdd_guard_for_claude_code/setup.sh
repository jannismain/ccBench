#!/bin/bash
set -e  # Exit on error

# Only install if not already present
if ! command -v tdd-guard &> /dev/null; then
    echo "Installing tdd-guard..."
    npm install -g tdd-guard
else
    echo "tdd-guard already installed"
fi

# Only create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

# Activate and install Python dependencies
source .venv/bin/activate
pip install --quiet tdd-guard-pytest

# Remind
echo "Activate and use the \`.venv\` virtual environment for any Python development." > CLAUDE.md
