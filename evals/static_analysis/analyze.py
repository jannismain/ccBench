#!/usr/bin/env python3
"""Run static analysis on changed Python and JavaScript/TypeScript files."""

import json
import os
import subprocess
import sys
from pathlib import Path

PYTHON_EXTENSIONS = {".py"}
JS_TS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
STATIC_ANALYSIS_EXTENSIONS = PYTHON_EXTENSIONS | JS_TS_EXTENSIONS
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".ruff_cache",
    ".svelte-kit",
    ".turbo",
    ".venv",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}


def get_changed_files(project_dir: Path) -> list[str]:
    """Get changed/new files using git, matching cloc eval's approach."""
    files = set()
    try:
        for command in changed_file_commands(project_dir):
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=project_dir,
            )
            if result.returncode == 0:
                files.update(
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                )
    except FileNotFoundError:
        return get_all_analyzable_files(project_dir)

    return sorted(files)


def changed_file_commands(project_dir: Path) -> list[list[str]]:
    baseline = ccbench_initial_commit(project_dir)
    if baseline is not None:
        return [
            ["git", "diff", "--name-only", "--diff-filter=ACMR", baseline, "--"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    if has_git_head(project_dir):
        return [
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ]
    return [["git", "ls-files"]]


def ccbench_initial_commit(project_dir: Path) -> str | None:
    result = subprocess.run(
        ["git", "log", "--grep=^ccbench initial state$", "--format=%H", "-1"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    commit = result.stdout.strip()
    if result.returncode == 0 and commit:
        return commit
    return None


def has_git_head(project_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    return result.returncode == 0


def get_all_analyzable_files(project_dir: Path) -> list[str]:
    """Fall back to all analyzable source files when git is unavailable."""
    files = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        root = Path(dirpath)
        for filename in filenames:
            path = root / filename
            if path.suffix in STATIC_ANALYSIS_EXTENSIONS:
                files.append(str(path.relative_to(project_dir)))
    return sorted(files)


def group_analyzable_files(files: list[str]) -> dict[str, list[str]]:
    grouped = {"python": [], "javascript": []}
    for file in files:
        if is_excluded(file):
            continue
        suffix = Path(file).suffix
        if suffix in PYTHON_EXTENSIONS:
            grouped["python"].append(file)
        elif suffix in JS_TS_EXTENSIONS:
            grouped["javascript"].append(file)
    return grouped


def is_excluded(file: str) -> bool:
    return any(part in EXCLUDED_DIRS for part in Path(file).parts)


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
            if result.returncode in {0, 1}:
                return []
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return None


def run_eslint(files: list[str], project_dir: Path) -> list[dict] | None:
    """Run ESLint and return parsed JSON output."""
    for cmd_prefix in eslint_commands(project_dir):
        try:
            result = subprocess.run(
                [*cmd_prefix, "--format", "json", *files],
                capture_output=True,
                text=True,
                cwd=project_dir,
            )
            if result.stdout.strip():
                return json.loads(result.stdout)
            if result.returncode in {0, 1}:
                return []
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return None


def eslint_commands(project_dir: Path) -> list[list[str]]:
    commands = []
    seen = set()

    for package_dir in package_json_dirs(project_dir):
        local_eslint = package_dir / "node_modules" / ".bin" / "eslint"
        if local_eslint.exists():
            command = [str(local_eslint)]
            commands.append(command)
            seen.add(tuple(command))

    for command in (["npx", "--yes", "eslint"], ["npm", "exec", "--yes", "--", "eslint"]):
        key = tuple(command)
        if key not in seen:
            commands.append(command)
            seen.add(key)
    return commands


def package_json_dirs(project_dir: Path) -> list[Path]:
    dirs = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        if "package.json" in filenames:
            dirs.append(Path(dirpath))
    return dirs or [project_dir]


def normalize_ruff_issues(issues: list[dict], project_dir: Path) -> list[dict]:
    details = []
    for issue in issues:
        code = issue.get("code", "")
        severity = "warning" if code.startswith("W") else "error"
        details.append(
            {
                "tool": "ruff",
                "file": relative_filename(issue.get("filename", ""), project_dir),
                "code": code,
                "message": issue.get("message", ""),
                "line": issue.get("location", {}).get("row"),
                "severity": severity,
            }
        )
    return details


def normalize_eslint_issues(results: list[dict], project_dir: Path) -> list[dict]:
    details = []
    for result in results:
        file = relative_filename(result.get("filePath", ""), project_dir)
        for message in result.get("messages", []):
            severity = eslint_severity(message)
            details.append(
                {
                    "tool": "eslint",
                    "file": file,
                    "code": message.get("ruleId") or "eslint",
                    "message": message.get("message", ""),
                    "line": message.get("line"),
                    "severity": severity,
                }
            )
    return details


def eslint_severity(message: dict) -> str:
    return "warning" if message.get("severity") == 1 else "error"


def relative_filename(filename: str, project_dir: Path) -> str:
    if not filename:
        return ""
    path = Path(filename)
    if not path.is_absolute():
        return filename
    try:
        return str(path.resolve().relative_to(project_dir.resolve()))
    except ValueError:
        return filename


def count_details(details: list[dict]) -> tuple[int, int]:
    errors = sum(1 for issue in details if issue["severity"] == "error")
    warnings = sum(1 for issue in details if issue["severity"] == "warning")
    return errors, warnings


def main():
    output_file = Path("static_analysis.json")
    project_dir = Path("project")

    if not project_dir.is_dir():
        result = {"status": "skipped", "reason": "no project directory found"}
        output_file.write_text(json.dumps(result, indent=2))
        print("No project directory found, skipping.")
        return

    files_by_language = group_analyzable_files(get_changed_files(project_dir))
    if not any(files_by_language.values()):
        result = {
            "status": "skipped",
            "reason": "no changed Python or JavaScript/TypeScript files found",
        }
        output_file.write_text(json.dumps(result, indent=2))
        print("No changed Python or JavaScript/TypeScript files found, skipping.")
        return

    details = []
    skipped_tools = []
    files_analyzed = 0

    python_files = files_by_language["python"]
    if python_files:
        ruff_output = run_ruff(python_files, project_dir)
        if ruff_output is None:
            skipped_tools.append({"tool": "ruff", "reason": "ruff not available"})
            print("ruff not available, skipping Python files.", file=sys.stderr)
        else:
            details.extend(normalize_ruff_issues(ruff_output, project_dir))
            files_analyzed += len(python_files)

    js_ts_files = files_by_language["javascript"]
    if js_ts_files:
        eslint_output = run_eslint(js_ts_files, project_dir)
        if eslint_output is None:
            skipped_tools.append({"tool": "eslint", "reason": "eslint not available"})
            print(
                "ESLint not available, skipping JavaScript/TypeScript files.",
                file=sys.stderr,
            )
        else:
            details.extend(normalize_eslint_issues(eslint_output, project_dir))
            files_analyzed += len(js_ts_files)

    if files_analyzed == 0:
        result = {
            "status": "skipped",
            "reason": "no supported static analysis tools available",
            "skipped_tools": skipped_tools,
        }
        output_file.write_text(json.dumps(result, indent=2))
        return

    errors, warnings = count_details(details)

    result = {
        "status": "completed",
        "lint_errors": errors,
        "lint_warnings": warnings,
        "files_analyzed": files_analyzed,
        "files_by_language": {k: len(v) for k, v in files_by_language.items()},
        "details": details,
    }
    if skipped_tools:
        result["skipped_tools"] = skipped_tools
    output_file.write_text(json.dumps(result, indent=2))
    print(
        f"Static analysis: {errors} errors, {warnings} warnings "
        f"in {files_analyzed} files"
    )


if __name__ == "__main__":
    main()
