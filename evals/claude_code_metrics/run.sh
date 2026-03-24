#!/bin/bash
# Extract metrics from Claude Code's output.json file

OUTPUT_FILE="output.json"
METRICS_FILE="claude_code_metrics.json"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "Error: $OUTPUT_FILE not found"
    exit 1
fi

python3 cc_metrics.py $OUTPUT_FILE $METRICS_FILE
