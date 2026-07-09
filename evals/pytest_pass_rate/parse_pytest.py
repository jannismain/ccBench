#!/usr/bin/env python3
"""Parse pytest output from run logs and produce test_pass_rate.json."""

import json
import re
import sys
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Matches the pytest summary line inside ====== markers
SUMMARY_RE = re.compile(r"=+\s+(.*?)\s+=+\s*$", re.MULTILINE)
# Matches individual count tokens like "4 passed", "1 failed", "2 error"
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|warnings?|deselected)")
# Matches duration at end of summary like "in 0.14s" or "in 1m 3.45s"
DURATION_RE = re.compile(r"\bin\s+(?:(\d+)m\s+)?(\d+(?:\.\d+)?)s\b")
# Matches FAILED lines like "FAILED test_solution.py::test_name - reason"
FAILED_RE = re.compile(r"FAILED\s+(\S+)")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def parse_pytest_summary(log_text: str) -> dict | None:
    """Extract test counts from a pytest summary line."""
    clean = strip_ansi(log_text)
    matches = list(SUMMARY_RE.finditer(clean))
    if not matches:
        return None

    # Use the last summary line (in case of multiple pytest runs)
    summary_text = matches[-1].group(1)
    counts = {}
    for count_match in COUNT_RE.finditer(summary_text):
        num = int(count_match.group(1))
        kind = count_match.group(2).rstrip("s")  # normalize "errors" -> "error"
        if kind == "warning":
            continue  # warnings aren't test outcomes
        counts[kind] = counts.get(kind, 0) + num

    if not counts:
        return None

    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    error = counts.get("error", 0)
    skipped = counts.get("skipped", 0)
    total = passed + failed + error + skipped

    duration_s = None
    duration_match = DURATION_RE.search(summary_text)
    if duration_match:
        minutes = int(duration_match.group(1) or 0)
        seconds = float(duration_match.group(2))
        duration_s = minutes * 60 + seconds

    return {
        "tests_run": total,
        "tests_passed": passed,
        "tests_failed": failed + error,
        "tests_skipped": skipped,
        "pass_rate": passed / total if total > 0 else 0.0,
        "duration_s": duration_s,
    }


def extract_failures(log_text: str) -> list[str]:
    clean = strip_ansi(log_text)
    return FAILED_RE.findall(clean)


def find_test_log() -> Path | None:
    """Find the run log that contains pytest output."""
    cwd = Path(".")
    for log_file in sorted(cwd.glob("run.*.log")):
        text = log_file.read_text(errors="replace")
        clean = strip_ansi(text)
        if SUMMARY_RE.search(clean):
            return log_file
    return None


def has_test_files() -> bool:
    cwd = Path(".")
    return bool(list(cwd.glob("test_*.py")) or list(cwd.glob("*_test.py")))


def main():
    output_file = Path("test_pass_rate.json")

    if not has_test_files():
        result = {"status": "skipped", "reason": "no test files found"}
        output_file.write_text(json.dumps(result, indent=2))
        print("No test files found, skipping.")
        return

    log_file = find_test_log()
    if log_file is None:
        result = {"status": "error", "reason": "test files found but no pytest output in logs"}
        output_file.write_text(json.dumps(result, indent=2))
        print("Warning: test files exist but no pytest output found in logs.", file=sys.stderr)
        return

    log_text = log_file.read_text(errors="replace")
    summary = parse_pytest_summary(log_text)
    if summary is None:
        result = {"status": "error", "reason": f"could not parse pytest summary from {log_file.name}"}
        output_file.write_text(json.dumps(result, indent=2))
        print(f"Warning: could not parse pytest output from {log_file.name}.", file=sys.stderr)
        return

    failures = extract_failures(log_text)
    result = {"status": "completed", **summary, "failures": failures}
    output_file.write_text(json.dumps(result, indent=2))

    print(f"Test pass rate: {summary['tests_passed']}/{summary['tests_run']}"
          f" ({summary['pass_rate']:.0%})")


if __name__ == "__main__":
    main()
