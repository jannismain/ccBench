#!/usr/bin/env python3
"""Run ruff static analysis on changed Python files and produce static_analysis.json."""

import json
import subprocess
import sys
from pathlib import Path


def get_changed_python_files(project_dir: Path) -> list[str]:
    """Get changed/new Python files using git, matching cloc eval's approach."""
    files = set()
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"],
            capture_output=True, text=True, cwd=project_dir,
        )
        if result.returncode == 0:
            files.update(line.strip() for line in result.stdout.splitlines() if line.strip())

        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=project_dir,
        )
        if result.returncode == 0:
            files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    except FileNotFoundError:
        # git not available, fall back to all Python files
        return [str(p.relative_to(project_dir)) for p in project_dir.rglob("*.py")]

    return [f for f in sorted(files) if f.endswith(".py")]


def run_ruff(files: list[str], project_dir: Path) -> list[dict] | None:
    """Run ruff check and return parsed JSON output."""
    for cmd_prefix in [["uvx", "ruff"], ["python3", "-m", "ruff"], ["ruff"]]:
        try:
            result = subprocess.run(
                [*cmd_prefix, "check", "--output-format", "json", *files],
                capture_output=True, text=True, cwd=project_dir,
            )
            # ruff exits 1 when it finds issues, which is normal
            if result.stdout.strip():
                return json.loads(result.stdout)
            return []
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return None


def main():
    output_file = Path("static_analysis.json")
    project_dir = Path("project")

    if not project_dir.is_dir():
        result = {"status": "skipped", "reason": "no project directory found"}
        output_file.write_text(json.dumps(result, indent=2))
        print("No project directory found, skipping.")
        return

    files = get_changed_python_files(project_dir)
    if not files:
        result = {"status": "skipped", "reason": "no changed Python files found"}
        output_file.write_text(json.dumps(result, indent=2))
        print("No changed Python files found, skipping.")
        return

    ruff_output = run_ruff(files, project_dir)
    if ruff_output is None:
        result = {"status": "skipped", "reason": "ruff not available"}
        output_file.write_text(json.dumps(result, indent=2))
        print("ruff not available, skipping.", file=sys.stderr)
        return

    errors = 0
    warnings = 0
    details = []
    for issue in ruff_output:
        code = issue.get("code", "")
        severity = "warning" if code.startswith("W") else "error"
        if severity == "error":
            errors += 1
        else:
            warnings += 1
        details.append({
            "file": issue.get("filename", ""),
            "code": code,
            "message": issue.get("message", ""),
            "line": issue.get("location", {}).get("row"),
            "severity": severity,
        })

    result = {
        "status": "completed",
        "lint_errors": errors,
        "lint_warnings": warnings,
        "files_analyzed": len(files),
        "details": details,
    }
    output_file.write_text(json.dumps(result, indent=2))
    print(f"Static analysis: {errors} errors, {warnings} warnings in {len(files)} files")


if __name__ == "__main__":
    main()
