#!/bin/bash
echo "Installing ccBench..."

cd project
CI=true NO_COLOR=1 uv sync

echo "Installed ccBench ✔"
