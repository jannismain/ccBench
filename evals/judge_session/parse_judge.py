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


def main():
    judge_output = Path("judge_output.json")
    output_file = Path("judge_scores.json")

    if not judge_output.exists():
        result = {"status": "error", "reason": "judge_output.json not found"}
        output_file.write_text(json.dumps(result, indent=2))
        print("Error: judge_output.json not found", file=sys.stderr)
        return

    scores = extract_json_from_response(judge_output)
    if scores is None:
        result = {"status": "error", "reason": "could not extract JSON scores from judge output"}
        output_file.write_text(json.dumps(result, indent=2))
        print("Error: could not extract structured scores from judge output", file=sys.stderr)
        return

    output_file.write_text(json.dumps(scores, indent=2))
    print(f"Judge scores extracted: {scores}")


if __name__ == "__main__":
    main()
