#!/usr/bin/env python3
"""Check whether the agent used a given skill, via the Skill tool or by reading its SKILL.md."""

import json
import sys
from pathlib import Path


def tool_uses(path: Path) -> list[dict]:
    uses = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        for block in obj.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses.append(block)
    return uses


def count_usage(uses: list[dict], skill: str) -> dict:
    invocations = 0
    reads = 0
    for use in uses:
        args = use.get("input") or {}
        if use.get("name") == "Skill" and str(args.get("skill", "")).split(":")[-1] == skill:
            invocations += 1
        elif use.get("name") == "Read" and str(args.get("file_path", "")).endswith(
            f"skills/{skill}/SKILL.md"
        ):
            reads += 1
    return {
        "skill": skill,
        "invocations": invocations,
        "skill_md_reads": reads,
        "used": int(invocations + reads > 0),
    }


COMPARE_METRICS_FILE = Path("metrics/skill_usage.json")


def write_compare_metrics(result: dict) -> None:
    """Contribute metrics to `ccbench compare`; None values render as missing."""
    metrics = {
        "used": {"value": result.get("used"), "label": "Skill used", "format": "{:.0%}"},
        "invocations": {"value": result.get("invocations"), "label": "Skill invocations", "format": "{:.1f}"},
    }
    COMPARE_METRICS_FILE.parent.mkdir(exist_ok=True)
    COMPARE_METRICS_FILE.write_text(json.dumps({"eval": "skill_usage", "metrics": metrics}, indent=2))


def main(output_file: Path, metrics_file: Path, skill: str) -> int:
    if not output_file.exists():
        result = {"status": "skipped", "reason": f"{output_file} not found", "skill": skill}
    else:
        result = {"status": "completed", **count_usage(tool_uses(output_file), skill)}
    metrics_file.write_text(json.dumps(result, indent=2) + "\n")
    write_compare_metrics(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]))
