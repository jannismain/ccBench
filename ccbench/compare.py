import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from numbers import Real
from pathlib import Path

import yaml

from . import paths
from .log import log

METRIC_DISPLAY = [
    ("cost_usd", "Cost (USD)", lambda v: f"${v:.4f}"),
    ("input_token_cost_usd", "Input token cost (USD)", lambda v: f"${v:.4f}"),
    ("cache_token_cost_usd", "Cache token cost (USD)", lambda v: f"${v:.4f}"),
    ("output_token_cost_usd", "Output token cost (USD)", lambda v: f"${v:.4f}"),
    ("duration_s", "Duration (s)", lambda v: f"{v:.1f}"),
    ("turns", "Turns", lambda v: str(int(v))),
    ("total_tokens", "Total tokens", lambda v: f"{int(v):,}"),
    ("input_tokens", "Input tokens", lambda v: f"{int(v):,}"),
    ("cache_tokens", "Cache tokens", lambda v: f"{int(v):,}"),
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
    ("exact_answer_score", "Exact answer", lambda v: f"{int(v)}/1"),
]

PERCENT_CHANGE_METRICS = {
    "cost_usd",
    "duration_s",
    "total_tokens",
    "output_tokens",
    "loc_total",
}


@dataclass(frozen=True)
class CompareRecord:
    task: str
    variant: str
    metrics: dict


def extract_from_claude_metrics(data: dict) -> dict:
    overall = data.get("overall", {})
    usage = overall.get("usage", {})
    token_metrics = extract_token_metrics(overall)
    return {
        "cost_usd": overall.get("total_cost_usd"),
        "input_token_cost_usd": token_metrics.get("input_token_cost_usd"),
        "cache_token_cost_usd": token_metrics.get("cache_token_cost_usd"),
        "output_token_cost_usd": token_metrics.get("output_token_cost_usd"),
        "duration_s": (overall.get("duration_ms") or 0) / 1000,
        "turns": overall.get("num_turns"),
        "total_tokens": token_metrics.get("total_tokens"),
        "input_tokens": token_metrics.get("input_tokens", usage.get("input_tokens")),
        "cache_tokens": token_metrics.get("cache_tokens"),
        "output_tokens": token_metrics.get("output_tokens", usage.get("output_tokens")),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "is_error": overall.get("is_error"),
    }


def extract_token_metrics(overall: dict) -> dict:
    model_usage = overall.get("model_usage")
    if isinstance(model_usage, dict) and model_usage:
        return extract_model_usage_token_metrics(model_usage)

    usage = overall.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    return allocate_token_costs(
        input_tokens=usage.get("input_tokens"),
        cache_tokens=sum_numbers(
            usage.get("cache_creation_input_tokens"),
            usage.get("cache_read_input_tokens"),
        ),
        output_tokens=usage.get("output_tokens"),
        cost_usd=overall.get("total_cost_usd"),
    )


def extract_model_usage_token_metrics(model_usage: dict) -> dict:
    result = empty_token_metrics()
    for model in model_usage.values():
        if not isinstance(model, dict):
            continue
        metrics = allocate_token_costs(
            input_tokens=get_number(model, "inputTokens", "input_tokens"),
            cache_tokens=sum_numbers(
                get_number(
                    model,
                    "cacheCreationInputTokens",
                    "cache_creation_input_tokens",
                ),
                get_number(model, "cacheReadInputTokens", "cache_read_input_tokens"),
            ),
            output_tokens=get_number(model, "outputTokens", "output_tokens"),
            cost_usd=get_number(model, "costUSD", "cost_usd", "total_cost_usd"),
        )
        add_token_metrics(result, metrics)
    return result


def allocate_token_costs(
    *,
    input_tokens,
    cache_tokens,
    output_tokens,
    cost_usd,
) -> dict:
    result = {
        "input_tokens": normalize_number(input_tokens),
        "cache_tokens": normalize_number(cache_tokens),
        "output_tokens": normalize_number(output_tokens),
        "total_tokens": None,
        "input_token_cost_usd": None,
        "cache_token_cost_usd": None,
        "output_token_cost_usd": None,
    }
    numeric_token_values = [
        value
        for value in (
            result["input_tokens"],
            result["cache_tokens"],
            result["output_tokens"],
        )
        if is_number(value)
    ]
    total_tokens = sum(numeric_token_values) if numeric_token_values else None
    result["total_tokens"] = total_tokens
    if is_number(cost_usd) and total_tokens:
        result["input_token_cost_usd"] = (
            cost_usd * (result["input_tokens"] or 0) / total_tokens
        )
        result["cache_token_cost_usd"] = (
            cost_usd * (result["cache_tokens"] or 0) / total_tokens
        )
        result["output_token_cost_usd"] = (
            cost_usd * (result["output_tokens"] or 0) / total_tokens
        )
    return result


def empty_token_metrics() -> dict:
    return {
        "input_tokens": 0,
        "cache_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_token_cost_usd": None,
        "cache_token_cost_usd": None,
        "output_token_cost_usd": None,
    }


def add_token_metrics(total: dict, metrics: dict) -> None:
    for key, value in metrics.items():
        if is_number(value):
            if not is_number(total[key]):
                total[key] = 0
            total[key] += value


def get_number(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if is_number(value):
            return value
    return None


def sum_numbers(*values) -> float | int | None:
    numeric_values = [value for value in values if is_number(value)]
    if not numeric_values:
        return None
    return sum(numeric_values)


def normalize_number(value):
    return value if is_number(value) else None


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


def extract_from_exact_answer(data: dict) -> dict:
    return {
        "exact_answer_score": data.get("score"),
        "exact_answer_matched": data.get("matched"),
    }


KNOWN_EVAL_FILES: dict[str, Callable[[dict], dict]] = {
    "claude_code_metrics.json": extract_from_claude_metrics,
    "cloc.json": extract_from_cloc,
    "test_pass_rate.json": extract_from_test_pass_rate,
    "static_analysis.json": extract_from_static_analysis,
    "judge_scores.json": extract_from_judge_scores,
    "exact_answer.json": extract_from_exact_answer,
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


def render_comparison_table(
    columns: list[str],
    metrics: list[dict],
    reference_indices: list[int | None] | None = None,
    metric_stats: list[dict] | None = None,
) -> str:
    reference_indices = reference_indices or infer_reference_indices(columns)
    rows = comparison_rows(metrics, reference_indices, metric_stats)
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


def comparison_rows(
    metrics: list[dict],
    reference_indices: list[int | None],
    metric_stats: list[dict] | None = None,
) -> list[tuple[str, list[str]]]:
    rows = []
    for key, label, fmt in METRIC_DISPLAY:
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            stats = [s.get(key) for s in metric_stats] if metric_stats else []
            formatted = format_metric_values(key, values, fmt, reference_indices, stats)
            rows.append((label, formatted))
    return rows


def format_metric_values(
    key: str,
    values: list,
    fmt: Callable,
    reference_indices: list[int | None],
    stats: list[dict | None],
) -> list[str]:
    changes = (
        percent_changes(values, reference_indices)
        if key in PERCENT_CHANGE_METRICS
        else []
    )
    formatted = []
    for index, value in enumerate(values):
        if value is None:
            formatted.append("\u2014")
            continue
        text = fmt(value)
        stat = stats[index] if index < len(stats) else None
        if stat is not None and stat.get("count", 0) > 1:
            text = f"{text} [{format_metric_range(stat, fmt)}]"
        change = changes[index] if index < len(changes) else None
        if change is not None:
            text = f"{text} ({format_percent_change(change)})"
        formatted.append(text)
    return formatted


def format_metric_range(stats: dict, fmt: Callable) -> str:
    return f"{fmt(stats['min'])}..{fmt(stats['max'])}"


def percent_changes(
    values: list, reference_indices: list[int | None]
) -> list[float | None]:
    changes = []
    for index, value in enumerate(values):
        reference_index = reference_indices[index] if index < len(reference_indices) else None
        if reference_index is None or reference_index == index:
            changes.append(None)
        else:
            changes.append(percent_change(values[reference_index], value))
    return changes


def percent_change(baseline, value) -> float | None:
    if not is_number(baseline) or not is_number(value) or baseline == 0:
        return None
    return ((value - baseline) / baseline) * 100


def is_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def format_percent_change(value: float) -> str:
    rounded = round(value, 1)
    if rounded == 0:
        return "+0%"
    if rounded.is_integer():
        return f"{rounded:+.0f}%"
    return f"{rounded:+.1f}%"


def render_comparison_json(
    columns: list[str],
    metrics: list[dict],
    reference_indices: list[int | None] | None = None,
) -> str:
    reference_indices = reference_indices or infer_reference_indices(columns)
    result = {"variants": columns, "metrics": {}, "metric_changes_pct": {}}
    for key, _label, _ in METRIC_DISPLAY:
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            result["metrics"][key] = values
            if key in PERCENT_CHANGE_METRICS and has_comparisons(reference_indices):
                result["metric_changes_pct"][key] = percent_changes(
                    values, reference_indices
                )
    if not result["metric_changes_pct"]:
        del result["metric_changes_pct"]
    return json.dumps(result, indent=2)


def render_grouped_comparison_table(task_results: dict[str, dict]) -> str:
    if not task_results:
        return "No metrics found."

    tables = []
    for task_name, task_data in task_results.items():
        table = render_comparison_table(
            variant_headers_with_counts(
                task_data["variants"],
                task_data["sample_counts"],
            ),
            task_data["metrics"],
            task_data["reference_indices"],
            task_data["metric_stats"],
        )
        tables.append(f"Task: {task_name}\n{table}")
    return "\n\n".join(tables)


def variant_headers_with_counts(variants: list[str], sample_counts: dict[str, int]) -> list[str]:
    return [f"{variant} (n={sample_counts.get(variant, 0)})" for variant in variants]


def render_grouped_comparison_json(task_results: dict[str, dict]) -> str:
    result = {"tasks": {}}
    for task_name, task_data in task_results.items():
        task_json = json.loads(
            render_comparison_json(
                task_data["variants"],
                task_data["metrics"],
                task_data["reference_indices"],
            )
        )
        task_json["sample_counts"] = task_data["sample_counts"]
        task_json["metric_stats"] = metric_stats_by_key(task_data["metric_stats"])
        result["tasks"][task_name] = task_json
    return json.dumps(result, indent=2)


def metric_stats_by_key(metric_stats: list[dict]) -> dict[str, list[dict | None]]:
    result = {}
    for key, _label, _fmt in METRIC_DISPLAY:
        values = [stats.get(key) for stats in metric_stats]
        if any(value is not None for value in values):
            result[key] = values
    return result


def build_task_results(task_entries: list[tuple[str, Path]]) -> dict[str, dict]:
    records = build_compare_records(task_entries)
    grouped: dict[str, dict[str, list[dict]]] = {}
    for record in records:
        grouped.setdefault(record.task, {}).setdefault(record.variant, []).append(
            record.metrics
        )

    task_results = {}
    for task_name, variants in grouped.items():
        variant_names = list(variants)
        metric_stats = [aggregate_metric_stats(samples) for samples in variants.values()]
        metrics = [mean_metrics(stats) for stats in metric_stats]
        reference_indices = reference_indices_for_variants(variant_names)
        task_results[task_name] = {
            "variants": variant_names,
            "metrics": metrics,
            "metric_stats": metric_stats,
            "reference_indices": reference_indices,
            "sample_counts": {
                variant: len(samples) for variant, samples in variants.items()
            },
        }
    return task_results


def reference_indices_for_variants(variants: list[str]) -> list[int | None]:
    if len(variants) < 2:
        return [None] * len(variants)
    reference_index = baseline_reference_index(list(enumerate(variants)))
    return [
        None if index == reference_index else reference_index
        for index in range(len(variants))
    ]


def build_compare_records(task_entries: list[tuple[str, Path]]) -> list[CompareRecord]:
    contexts = task_variant_contexts(task_entries)
    return [
        CompareRecord(
            task=context[0],
            variant=context[1],
            metrics=extract_metrics_summary(path),
        )
        for context, (_label, path) in zip(contexts, task_entries, strict=True)
    ]


def task_variant_contexts(task_entries: list[tuple[str, Path]]) -> list[tuple[str, str]]:
    metadata_contexts = [
        experiment_task_variant_context(path) for _label, path in task_entries
    ]
    if any(context is not None for context in metadata_contexts) or any(
        has_experiment_config(path) for _label, path in task_entries
    ):
        return [
            (context[1], context[2]) if context else fallback_task_variant(label, path)
            for context, (label, path) in zip(
                metadata_contexts, task_entries, strict=True
            )
        ]
    return inferred_task_variant_contexts(task_entries)


def inferred_task_variant_contexts(
    task_entries: list[tuple[str, Path]]
) -> list[tuple[str, str]]:
    leaves = [entry_leaf(label, path) for label, path in task_entries]
    groups = infer_column_variant_groups(leaves)
    contexts: list[tuple[str, str] | None] = [None] * len(task_entries)
    for task_name, entries in groups.items():
        for index, variant_name in entries:
            contexts[index] = (task_name, variant_name)
    return [
        context or fallback_task_variant(label, path)
        for context, (label, path) in zip(contexts, task_entries, strict=True)
    ]


def fallback_task_variant(label: str, path: Path) -> tuple[str, str]:
    leaf = entry_leaf(label, path)
    if path.parent.name == "tasks" and "/" in label:
        return leaf, label.rsplit("/", 1)[0]
    if path.parent.name == "tasks":
        return leaf, implicit_variant_name(load_experiment_config_for_result(path.parent.parent))
    return leaf, "default"


def entry_leaf(label: str, path: Path) -> str:
    if label:
        return label.rsplit("/", 1)[-1]
    return path.name


def aggregate_metric_stats(samples: list[dict]) -> dict:
    stats = {}
    for key, _label, _fmt in METRIC_DISPLAY:
        value = aggregate_metric_value_stats([sample.get(key) for sample in samples])
        if value:
            stats[key] = value
    return stats


def mean_metrics(metric_stats: dict) -> dict:
    return {key: stats["mean"] for key, stats in metric_stats.items()}


def aggregate_metric_value_stats(values: list) -> dict | None:
    numeric_values = [value for value in values if is_number(value)]
    if not numeric_values:
        return None
    total = sum(numeric_values)
    average = total / len(numeric_values)
    mean = int(average) if average.is_integer() else average
    return {
        "mean": mean,
        "min": min(numeric_values),
        "max": max(numeric_values),
        "count": len(numeric_values),
    }


def has_comparisons(reference_indices: list[int | None]) -> bool:
    return any(index is not None for index in reference_indices)


def reference_indices_for_task_entries(
    task_entries: list[tuple[str, Path]]
) -> list[int | None]:
    contexts = [
        experiment_task_variant_context(path) for _label, path in task_entries
    ]
    if any(context is not None for context in contexts) or any(
        has_experiment_config(path) for _label, path in task_entries
    ):
        return reference_indices_from_contexts(contexts)
    return infer_reference_indices([label for label, _path in task_entries])


def has_experiment_config(path: Path) -> bool:
    return (
        path.parent.name == "tasks"
        and load_experiment_config_for_result(path.parent.parent) is not None
    )


def experiment_task_variant_context(path: Path) -> tuple[Path, str, str] | None:
    if path.parent.name != "tasks":
        return None

    experiment_root = path.parent.parent
    config = load_experiment_config_for_result(experiment_root)
    if not config:
        return None

    variants = list((config.get("variants") or {}).keys())
    if not variants:
        for task_name in config.get("tasks") or []:
            if path.name == str(task_name):
                return (
                    experiment_root.resolve(),
                    str(task_name),
                    implicit_variant_name(config),
                )
        return None

    dirname = path.name
    for task_name in config.get("tasks") or []:
        for variant_name in variants:
            if dirname == f"{task_name}_{variant_name}":
                return experiment_root.resolve(), str(task_name), str(variant_name)
    return None


def implicit_variant_name(config: dict | None) -> str:
    configs = (config or {}).get("configs") or []
    if not configs:
        return "default"
    names = [str(parse_config_name(config_entry)) for config_entry in configs]
    return "+".join(names)


def parse_config_name(config_entry) -> str:
    if isinstance(config_entry, str):
        return config_entry
    if isinstance(config_entry, dict) and config_entry:
        return str(next(iter(config_entry)))
    return "default"


def load_experiment_config_for_result(experiment_root: Path) -> dict | None:
    for config_file in sorted(
        list(experiment_root.glob("*.yaml")) + list(experiment_root.glob("*.yml"))
    ):
        try:
            data = yaml.safe_load(config_file.read_text())
        except yaml.YAMLError as e:
            log.warning(f"Failed to parse experiment config {config_file}: {e}")
            continue
        if isinstance(data, dict):
            return data
    return None


def reference_indices_from_contexts(
    contexts: list[tuple[Path, str, str] | None]
) -> list[int | None]:
    groups: dict[tuple[Path, str], list[tuple[int, str]]] = {}
    for index, context in enumerate(contexts):
        if context is None:
            continue
        experiment_root, task_name, variant_name = context
        groups.setdefault((experiment_root, task_name), []).append((index, variant_name))
    return reference_indices_from_groups(len(contexts), groups.values())


def infer_reference_indices(columns: list[str]) -> list[int | None]:
    groups = infer_column_variant_groups(columns)
    return reference_indices_from_groups(len(columns), groups.values())


def infer_column_variant_groups(columns: list[str]) -> dict[str, list[tuple[int, str]]]:
    leaves = [column.rsplit("/", 1)[-1] for column in columns]
    baseline_groups = infer_baseline_column_groups(leaves)
    if baseline_groups:
        return baseline_groups
    return infer_suffix_column_groups(leaves)


def infer_baseline_column_groups(leaves: list[str]) -> dict[str, list[tuple[int, str]]]:
    baseline_prefixes = []
    for leaf in leaves:
        if "baseline" in leaf.lower():
            prefix, variant = split_task_variant(leaf)
            baseline_prefixes.append((prefix, variant))

    if not baseline_prefixes:
        return {}

    if all(prefix == "" for prefix, _variant in baseline_prefixes):
        return {"": [(index, leaf) for index, leaf in enumerate(leaves)]}

    groups: dict[str, list[tuple[int, str]]] = {}
    for index, leaf in enumerate(leaves):
        matches: list[str] = [
            prefix
            for prefix, _variant in baseline_prefixes
            if prefix and leaf.startswith(f"{prefix}_")
        ]
        if not matches:
            continue
        prefix = matches[0]
        for candidate in matches[1:]:
            if len(candidate) > len(prefix):
                prefix = candidate
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append((index, leaf[len(prefix) + 1 :]))
    return groups


def infer_suffix_column_groups(leaves: list[str]) -> dict[str, list[tuple[int, str]]]:
    groups: dict[str, list[tuple[int, str]]] = {}
    for index, leaf in enumerate(leaves):
        prefix, variant = split_task_variant(leaf)
        if not prefix:
            continue
        groups.setdefault(prefix, []).append((index, variant))
    return {prefix: group for prefix, group in groups.items() if len(group) > 1}


def split_task_variant(label: str) -> tuple[str, str]:
    if "_" not in label:
        return "", label
    task_name, variant_name = label.rsplit("_", 1)
    return task_name, variant_name


def reference_indices_from_groups(
    result_count: int, groups: Iterable[Iterable[tuple[int, str]]]
) -> list[int | None]:
    reference_indices: list[int | None] = [None] * result_count
    for group in groups:
        entries = list(group)
        if len(entries) < 2:
            continue
        reference_index = baseline_reference_index(entries)
        for index, _variant_name in entries:
            if index != reference_index:
                reference_indices[index] = reference_index
    return reference_indices


def baseline_reference_index(entries: list[tuple[int, str]]) -> int:
    for index, variant_name in entries:
        if "baseline" in variant_name.lower():
            return index
    return entries[0][0]


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

    task_results = build_task_results(task_entries)

    if json_output:
        print(render_grouped_comparison_json(task_results))
    else:
        print(render_grouped_comparison_table(task_results))


def most_recent_result_dir() -> list[str]:
    candidates = sorted(paths.RESULTS.iterdir()) if paths.RESULTS.is_dir() else []
    candidates = [d for d in candidates if d.is_dir()]
    if not candidates:
        sys.exit("No result directories found and RESULTS directory is empty.")
    log.info(f"Defaulting to most recent result: {candidates[-1].name}")
    return [str(candidates[-1])]
