#!/bin/bash
echo "RELEVANT ENVIRONMENT VARIABLES:"
env | grep PORTKEY_
env | grep ANTHROPIC_
env | grep CLAUDE_CODE_

read -rp "Enter env var prefix to show (leave empty to skip): " env_prefix
if [ -n "$env_prefix" ]; then
    echo "MATCHING VARIABLES (${env_prefix}*):"
    while IFS= read -r line; do
        case "$line" in
            "${env_prefix}"*) echo "$line" ;;
        esac
    done < <(env)
fi
