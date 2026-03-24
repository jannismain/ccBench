#!/usr/bin/env python3

import json
import logging
import sys


def aggregate_model_usage(metrics: list[dict]) -> dict:
    models = {
        model
        for metric in metrics
        if isinstance(metric.get("model_usage"), dict)
        for model in metric["model_usage"]
    }
    result = {}
    for model in models:
        entries = [
            metric["model_usage"][model]
            for metric in metrics
            if isinstance(metric.get("model_usage"), dict) and model in metric["model_usage"]
        ]
        if not entries:
            continue
        if isinstance(entries[0], dict):
            merged = {}
            for entry in entries:
                for k, v in entry.items():
                    if isinstance(v, (int, float)):
                        merged[k] = merged.get(k, 0) + v
                    else:
                        merged[k] = v
            result[model] = merged
        else:
            result[model] = sum(e for e in entries if e is not None)
    return result


def extract_metrics(content: list[dict | list]) -> dict:
    result_entries = []
    for obj in content:
        if isinstance(obj, dict) and obj.get("type") == "result":
            result_entries += [obj]

    if not result_entries:
        print(f"Error: No result entry found in {cc_jsonl_output}", file=sys.stderr)
        sys.exit(1)

    metrics = []
    for result_entry in result_entries:
        # Extract metrics
        usage = result_entry.get("usage", {}) or {}
        model_usage = result_entry.get("modelUsage", {}) or {}
        server_tool_use = usage.get("server_tool_use", {}) if isinstance(usage, dict) else {}
        permission_denials = result_entry.get("permission_denials", []) or []

        m = {
            "duration_ms": result_entry.get("duration_ms"),
            "duration_api_ms": result_entry.get("duration_api_ms"),
            "num_turns": result_entry.get("num_turns"),
            "total_cost_usd": result_entry.get("total_cost_usd"),
            "is_error": result_entry.get("is_error", False),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0)
                if isinstance(usage, dict)
                else 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0)
                if isinstance(usage, dict)
                else 0,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0)
                if isinstance(usage, dict)
                else 0,
                "output_tokens": usage.get("output_tokens", 0)
                if isinstance(usage, dict)
                else 0,
                "web_search_requests": server_tool_use.get("web_search_requests", 0)
                if isinstance(server_tool_use, dict)
                else 0,
            },
            "model_usage": model_usage if isinstance(model_usage, dict) else {},
            "permission_denials": permission_denials
            if isinstance(permission_denials, list)
            else [],
            "num_permission_denials": len(permission_denials)
            if isinstance(permission_denials, list)
            else 0,
        }
        metrics += [m]

    overall = {
        "duration_ms": sum(m["duration_ms"] for m in metrics if m["duration_ms"] is not None),
        "duration_api_ms": sum(
            m["duration_api_ms"] for m in metrics if m["duration_api_ms"] is not None
        ),
        "num_turns": sum(m["num_turns"] for m in metrics if m["num_turns"] is not None),
        "total_cost_usd": sum(
            m["total_cost_usd"] for m in metrics if m["total_cost_usd"] is not None
        ),
        "is_error": any(m["is_error"] for m in metrics),
        "usage": {
            "input_tokens": sum(
                m["usage"]["input_tokens"]
                for m in metrics
                if m["usage"]["input_tokens"] is not None
            ),
            "cache_creation_input_tokens": sum(
                m["usage"]["cache_creation_input_tokens"]
                for m in metrics
                if m["usage"]["cache_creation_input_tokens"] is not None
            ),
            "cache_read_input_tokens": sum(
                m["usage"]["cache_read_input_tokens"]
                for m in metrics
                if m["usage"]["cache_read_input_tokens"] is not None
            ),
            "output_tokens": sum(
                m["usage"]["output_tokens"]
                for m in metrics
                if m["usage"]["output_tokens"] is not None
            ),
            "web_search_requests": sum(
                m["usage"]["web_search_requests"]
                for m in metrics
                if m["usage"]["web_search_requests"] is not None
            ),
        },
        "model_usage": aggregate_model_usage(metrics),
        "permission_denials": [
            denial
            for m in metrics
            for denial in m["permission_denials"]
            if isinstance(m["permission_denials"], list)
        ],
        "num_permission_denials": sum(
            m["num_permission_denials"]
            for m in metrics
            if m["num_permission_denials"] is not None
        ),
    }

    return dict(overall=overall, steps=metrics)


def main(cc_jsonl_output: str, target: str):
    with open(cc_jsonl_output, "r") as f:
        content = [json.loads(line) for line in f.readlines() if line.strip()]

    metrics = extract_metrics(content)

    # Write metrics to file
    with open(target, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Metrics extracted successfully to {target}")
    overall = metrics["overall"]
    # Print summary
    print("\nSummary:")
    print(
        f"  Duration: {overall['duration_ms']}ms ({overall['duration_ms'] // 60000:d}m {overall['duration_ms'] // 1000 % 60:d}s)"
    )
    print(
        f"  API Duration: {overall['duration_api_ms']}ms ({overall['duration_api_ms'] // 60000:d}m {overall['duration_api_ms'] // 1000 % 60:d}s)"
    )
    print(f"  Turns: {overall['num_turns']}")
    print(f"  Cost: ${overall['total_cost_usd']:.6f}")
    print(f"  Error: {overall['is_error']}")
    print(f"  Input tokens: {overall['usage']['input_tokens']}")
    print(f"  Cache creation tokens: {overall['usage']['cache_creation_input_tokens']}")
    print(f"  Cache read tokens: {overall['usage']['cache_read_input_tokens']}")
    print(f"  Output tokens: {overall['usage']['output_tokens']}")
    print(f"  Permission denials: {overall['num_permission_denials']}")


if __name__ == "__main__":
    import sys

    cc_jsonl_output = sys.argv[1] if len(sys.argv) > 1 else "../output.json"
    target = sys.argv[2] if len(sys.argv) > 2 else "../claude_code_metrics.json"

    try:
        main(cc_jsonl_output, target)
    except Exception as e:
        logging.exception(f"Error extracting metrics: {e}", exc_info=True)
        sys.exit(1)
