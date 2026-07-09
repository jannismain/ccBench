#!/bin/bash

if [ ! -f .env ]; then
    cp .env.sample .env
fi

# Find all lines in .env.sample whose value ends with "..."
while IFS= read -r line <&3; do
    # Match "export VAR=..." or "VAR=..." (value ends with "...")
    if [[ "$line" =~ ^(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*\.\.\.$ ]]; then
        var_name="${BASH_REMATCH[2]}"

        read -r -p "Enter value for ${var_name}: " secret_value
        # Replace the entire line in .env (handles optional 'export ' prefix)
        sed -i '' "s|^\(export \)\{0,1\}${var_name}=.*|export ${var_name}=\"${secret_value}\"|" .env
    fi
done 3< .env.sample

source .env
