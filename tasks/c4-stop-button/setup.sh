#!/bin/bash
echo "Installing c4-genai-suite..."

cd project
CI=true NO_COLOR=1 npm install

echo "Installed c4-genai-suite"
