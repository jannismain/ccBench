#!/bin/bash

mkdir project && cd project

if command -v openspec &> /dev/null; then
    version=$(openspec --version | grep -oE '[0-9]+\.[0-9]+')
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    echo "openspec version: $version"
    if [[ "$major" -lt 1 || ("$major" -eq 1 && "$minor" -lt 4) ]]; then
        echo "openspec version $version is less than 1.4, upgrading..."
        npm install -g @fission-ai/openspec
    fi
else
    npm install -g @fission-ai/openspec
fi
openspec init --tools ${TOOLS:-claude}
