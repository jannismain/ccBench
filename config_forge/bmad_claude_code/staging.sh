#!/usr/bin/env bash
# Install BMAD framework files into the staging directory

# Set terminal size — the bmad installer (clack/prompts) queries terminal
# dimensions for its UI. In a pty without COLUMNS/LINES set, it can get
# negative values causing "Invalid count value" errors.
export COLUMNS="${COLUMNS:-120}"
export LINES="${LINES:-40}"

echo "Installing BMAD framework..."
CI=true NO_COLOR=1 npx bmad-method@next --version
CI=true NO_COLOR=1 npx bmad-method@next install --yes --tools claude-code --modules bmm --directory project
echo "BMAD framework installed"
