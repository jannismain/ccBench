#!/bin/bash
# Extract metrics from Claude Code's output.json file

OUTPUT_FILE="output.json"
METRICS_FILE="claude_code_metrics.json"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "Error: $OUTPUT_FILE not found"
    exit 1
fi

# Extract the last JSON object (the result summary) from output.json
# The file contains multiple JSON objects, one per line
python3 << 'EOF'
import json
import sys

try:
    # Read the entire file and split by lines starting with {
    with open('output.json', 'r') as f:
        content = f.read()

    # Split into JSON objects (each starts with { at the beginning of a line)
    # We need to find JSON objects that span multiple lines
    objects = []
    current_obj = ""
    brace_count = 0

    for char in content:
        if char == '{':
            if brace_count == 0:
                current_obj = ""
            brace_count += 1

        current_obj += char

        if char == '}':
            brace_count -= 1
            if brace_count == 0:
                try:
                    obj = json.loads(current_obj)
                    objects.append(obj)
                except json.JSONDecodeError:
                    pass
                current_obj = ""

    # Find the result entry (type: "result")
    result_entry = None
    for obj in reversed(objects):
        if isinstance(obj, dict) and obj.get('type') == 'result':
            result_entry = obj
            break

    if not result_entry:
        print("Error: No result entry found in output.json", file=sys.stderr)
        sys.exit(1)

    # Extract metrics
    usage = result_entry.get('usage', {}) or {}
    model_usage = result_entry.get('modelUsage', {}) or {}
    server_tool_use = usage.get('server_tool_use', {}) if isinstance(usage, dict) else {}
    permission_denials = result_entry.get('permission_denials', []) or []

    metrics = {
        'duration_ms': result_entry.get('duration_ms'),
        'duration_api_ms': result_entry.get('duration_api_ms'),
        'num_turns': result_entry.get('num_turns'),
        'total_cost_usd': result_entry.get('total_cost_usd'),
        'is_error': result_entry.get('is_error', False),
        'usage': {
            'input_tokens': usage.get('input_tokens', 0) if isinstance(usage, dict) else 0,
            'cache_creation_input_tokens': usage.get('cache_creation_input_tokens', 0) if isinstance(usage, dict) else 0,
            'cache_read_input_tokens': usage.get('cache_read_input_tokens', 0) if isinstance(usage, dict) else 0,
            'output_tokens': usage.get('output_tokens', 0) if isinstance(usage, dict) else 0,
            'web_search_requests': server_tool_use.get('web_search_requests', 0) if isinstance(server_tool_use, dict) else 0,
        },
        'model_usage': model_usage if isinstance(model_usage, dict) else {},
        'permission_denials': permission_denials if isinstance(permission_denials, list) else [],
        'num_permission_denials': len(permission_denials) if isinstance(permission_denials, list) else 0
    }

    # Write metrics to file
    with open('claude_code_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)

    print("Metrics extracted successfully to claude_code_metrics.json")

    # Print summary
    print(f"\nSummary:")
    print(f"  Duration: {metrics['duration_ms']}ms ({metrics['duration_ms']/1000:.2f}s)")
    print(f"  API Duration: {metrics['duration_api_ms']}ms ({metrics['duration_api_ms']/1000:.2f}s)")
    print(f"  Turns: {metrics['num_turns']}")
    print(f"  Cost: ${metrics['total_cost_usd']:.6f}")
    print(f"  Error: {metrics['is_error']}")
    print(f"  Input tokens: {metrics['usage']['input_tokens']}")
    print(f"  Cache creation tokens: {metrics['usage']['cache_creation_input_tokens']}")
    print(f"  Cache read tokens: {metrics['usage']['cache_read_input_tokens']}")
    print(f"  Output tokens: {metrics['usage']['output_tokens']}")
    print(f"  Permission denials: {metrics['num_permission_denials']}")

except Exception as e:
    print(f"Error extracting metrics: {e}", file=sys.stderr)
    sys.exit(1)
EOF
