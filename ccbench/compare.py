import json
import sys
from collections.abc import Callable
from pathlib import Path

from . import paths
from .log import log

METRIC_DISPLAY = [
    ("cost_usd", "Cost (USD)", lambda v: f"${v:.4f}"),
    ("duration_s", "Duration (s)", lambda v: f"{v:.1f}"),
    ("turns", "Turns", lambda v: str(int(v))),
    ("output_tokens", "Output tokens", lambda v: f"{int(v):,}"),
    ("loc_total", "Lines of code", lambda v: str(int(v))),
    ("test_pass_rate", "Test pass rate", lambda v: f"{v:.0%}"),
    ("tests_passed", "Tests passed", lambda v: str(int(v))),
    ("tests_failed", "Tests failed", lambda v: str(int(v))),
    ("test_duration_s", "Test duration (s)", lambda v: f"{v:.2f}"),
    ("lint_errors", "Lint errors", lambda v: str(int(v))),
    ("lint_warnings", "Lint warnings", lambda v: str(int(v))),
    ("judge_readability", "Readability", lambda v: f"{int(v)}/5"),
    ("judge_idiomatic", "Idiomatic style", lambda v: f"{int(v)}/5"),
    ("judge_error_handling", "Error handling", lambda v: f"{int(v)}/5"),
    ("judge_efficiency", "Efficiency", lambda v: f"{int(v)}/5"),
]


def extract_from_claude_metrics(data: dict) -> dict:
    overall = data.get("overall", {})
    usage = overall.get("usage", {})
    return {
        "cost_usd": overall.get("total_cost_usd"),
        "duration_s": (overall.get("duration_ms") or 0) / 1000,
        "turns": overall.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "is_error": overall.get("is_error"),
    }


def extract_from_cloc(data: dict) -> dict:
    summary = data.get("SUM", {})
    return {
        "loc_total": summary.get("code"),
        "loc_files": summary.get("nFiles"),
    }


def extract_from_test_pass_rate(data: dict) -> dict:
    if data.get("status") == "skipped":
        return {
            "test_pass_rate": None,
            "tests_passed": None,
            "tests_failed": None,
            "test_duration_s": None,
        }
    return {
        "test_pass_rate": data.get("pass_rate"),
        "tests_passed": data.get("tests_passed"),
        "tests_failed": data.get("tests_failed"),
        "test_duration_s": data.get("duration_s"),
    }


def extract_from_static_analysis(data: dict) -> dict:
    if data.get("status") == "skipped":
        return {"lint_errors": None, "lint_warnings": None}
    return {
        "lint_errors": data.get("lint_errors"),
        "lint_warnings": data.get("lint_warnings"),
    }


def extract_from_judge_scores(data: dict) -> dict:
    return {
        "judge_readability": data.get("readability"),
        "judge_idiomatic": data.get("idiomatic_style"),
        "judge_error_handling": data.get("error_handling"),
        "judge_efficiency": data.get("efficiency"),
    }


KNOWN_EVAL_FILES: dict[str, Callable[[dict], dict]] = {
    "claude_code_metrics.json": extract_from_claude_metrics,
    "cloc.json": extract_from_cloc,
    "test_pass_rate.json": extract_from_test_pass_rate,
    "static_analysis.json": extract_from_static_analysis,
    "judge_scores.json": extract_from_judge_scores,
}


def extract_metrics_summary(task_dir: Path) -> dict:
    metrics = {}
    for filename, extractor in KNOWN_EVAL_FILES.items():
        filepath = task_dir / filename
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text())
                metrics.update(extractor(data))
            except (json.JSONDecodeError, KeyError) as e:
                log.warning(f"Failed to parse {filepath}: {e}")
    return metrics


def resolve_task_dirs(result_dirs: list[str], across: bool) -> list[tuple[str, Path]]:
    """Resolve input paths to (label, task_dir) tuples."""
    entries = []
    for raw in result_dirs:
        d = Path(raw)
        if not d.is_dir():
            log.warning(f"Not a directory: {d}")
            continue
        entries.extend(resolve_result_dir(d, across))
    return entries


def resolve_result_dir(result_dir: Path, across: bool) -> list[tuple[str, Path]]:
    tasks_subdir = result_dir / "tasks"
    if tasks_subdir.is_dir():
        return [
            (task_label(result_dir, child, across), child)
            for child in sorted(tasks_subdir.iterdir())
            if child.is_dir()
        ]
    if (result_dir / "project").is_dir() or (result_dir / "output.json").exists():
        label = result_dir.name
        if across and result_dir.parent.name == "tasks":
            label = f"{result_dir.parent.parent.name}/{result_dir.name}"
        return [(label, result_dir)]
    log.warning(f"Cannot identify directory type: {result_dir}")
    return []


def task_label(run_dir: Path, task_dir: Path, across: bool) -> str:
    if across:
        return f"{run_dir.name}/{task_dir.name}"
    return task_dir.name


def render_comparison_table(columns: list[str], metrics: list[dict]) -> str:
    rows = comparison_rows(metrics)
    if not rows:
        return "No metrics found."

    label_width = max(len(label) for label, _ in rows)
    col_widths = [
        max(len(col), *(len(row[1][i]) for row in rows))
        for i, col in enumerate(columns)
    ]

    header = " " * (label_width + 2)
    header += "  ".join(col.ljust(w) for col, w in zip(columns, col_widths, strict=True))
    lines = [header]
    for label, formatted in rows:
        line = label.ljust(label_width) + "  "
        line += "  ".join(v.ljust(w) for v, w in zip(formatted, col_widths, strict=True))
        lines.append(line)
    return "\n".join(lines)


def comparison_rows(metrics: list[dict]) -> list[tuple[str, list[str]]]:
    rows = []
    for key, label, fmt in METRIC_DISPLAY:
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            formatted = [fmt(v) if v is not None else "\u2014" for v in values]
            rows.append((label, formatted))
    return rows


def render_comparison_json(columns: list[str], metrics: list[dict]) -> str:
    result = {"variants": columns, "metrics": {}}
    for key, _label, _ in METRIC_DISPLAY:
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            result["metrics"][key] = values
    return json.dumps(result, indent=2)


def cmd_compare(
    result_dirs: list[str] | None = None,
    *,
    across: bool = False,
    json_output: bool = False,
) -> None:
    resolved_result_dirs = result_dirs or most_recent_result_dir()
    task_entries = resolve_task_dirs(resolved_result_dirs, across)
    if not task_entries:
        sys.exit("No task directories found.")

    columns = [label for label, _ in task_entries]
    metrics = [extract_metrics_summary(path) for _, path in task_entries]

    if json_output:
        print(render_comparison_json(columns, metrics))
    else:
        print(render_comparison_table(columns, metrics))


def most_recent_result_dir() -> list[str]:
    candidates = sorted(paths.RESULTS.iterdir()) if paths.RESULTS.is_dir() else []
    candidates = [d for d in candidates if d.is_dir()]
    if not candidates:
        sys.exit("No result directories found and RESULTS directory is empty.")
    log.info(f"Defaulting to most recent result: {candidates[-1].name}")
    return [str(candidates[-1])]
