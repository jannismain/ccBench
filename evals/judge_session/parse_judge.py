#!/usr/bin/env python3
"""Extract structured judge scores from Claude's JSONL output."""

import json
import re
import sys
from pathlib import Path


def extract_json_from_response(judge_output_path: Path) -> dict | None:
    """Extract JSON scores from the judge's JSONL stream output."""
    text_parts = []
    with judge_output_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "assistant":
                continue

            message = obj.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

    full_text = "\n".join(text_parts)
    if not full_text.strip():
        return None

    # Strip markdown code fences if present
    full_text = re.sub(r"```json\s*", "", full_text)
    full_text = re.sub(r"```\s*", "", full_text)

    # Find the first JSON object in the text
    match = re.search(r"\{[^{}]*\}", full_text, re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


COMPARE_METRICS_FILE = Path("metrics/judge_session.json")


def write_compare_metrics(scores: dict) -> None:
    """Contribute metrics to `ccbench compare`; None values render as missing."""
    labels = {
        "readability": "Readability",
        "idiomatic_style": "Idiomatic style",
        "error_handling": "Error handling",
        "efficiency": "Efficiency",
    }
    metrics = {
        key: {"value": scores.get(key), "label": label, "format": "{:.0f}/5"}
        for key, label in labels.items()
    }
    COMPARE_METRICS_FILE.parent.mkdir(exist_ok=True)
    COMPARE_METRICS_FILE.write_text(json.dumps({"eval": "judge_session", "metrics": metrics}, indent=2))


def main():
    judge_output = Path("judge_output.json")
    output_file = Path("judge_scores.json")

    if not judge_output.exists():
        result = {"status": "error", "reason": "judge_output.json not found"}
        output_file.write_text(json.dumps(result, indent=2))
        write_compare_metrics({})
        print("Error: judge_output.json not found", file=sys.stderr)
        return

    scores = extract_json_from_response(judge_output)
    if scores is None:
        result = {"status": "error", "reason": "could not extract JSON scores from judge output"}
        output_file.write_text(json.dumps(result, indent=2))
        write_compare_metrics({})
        print("Error: could not extract structured scores from judge output", file=sys.stderr)
        return

    output_file.write_text(json.dumps(scores, indent=2))
    write_compare_metrics(scores)
    print(f"Judge scores extracted: {scores}")


if __name__ == "__main__":
    main()
