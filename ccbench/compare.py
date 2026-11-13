import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path

import yaml

from . import paths
from .legacy_metrics import read_legacy_metrics, upgrade_legacy_values
from .log import log

METRICS_DIR = "metrics"


@dataclass(frozen=True)
class CompareRecord:
    task: str
    variant: str
    metrics: dict
    specs: dict = field(default_factory=dict)


def load_metrics(task_dir: Path, eval_order: Iterable[str] = ()) -> tuple[dict, dict]:
    """Read values and display specs contributed by evals via `metrics/<eval>.json`.

    Keys are namespaced as `<eval>.<metric>`. Results recorded before evals wrote
    metrics files fall back to the frozen legacy extractors.
    """
    files = sorted((task_dir / METRICS_DIR).glob("*.json"))
    if not files:
        return read_legacy_metrics(task_dir)
    order = {name: index for index, name in enumerate(eval_order)}
    files.sort(key=lambda path: (order.get(path.stem, len(order)), path.stem))
    values: dict = {}
    specs: dict = {}
    for path in files:
        try:
            data = json.loads(path.read_text())
            eval_name = str(data.get("eval") or path.stem)
            metrics = data["metrics"]
            entries = metrics.items()
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            log.warning(f"Failed to parse {path}: {e}")
            continue
        for name, entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
                log.warning(f"Ignoring malformed metric '{name}' in {path}")
                continue
            key = f"{eval_name}.{name}"
            value = entry.get("value")
            values[key] = value if is_number(value) else None
            specs[key] = metric_spec(entry)
    return values, specs


def metric_spec(entry: dict) -> dict:
    spec = {"label": entry["label"], "format": str(entry.get("format") or "{}")}
    if entry.get("change") == "percent":
        spec["change"] = "percent"
    return spec


def manifest_metrics(manifest: dict, task_dir: Path) -> tuple[dict, dict]:
    """Return metric values and specs, preferring those recorded in the manifest."""
    if "metrics" not in manifest:
        return load_metrics(task_dir, eval_names(manifest))
    if "metric_specs" not in manifest:
        return upgrade_legacy_values(manifest["metrics"])
    specs = manifest["metric_specs"]
    if isinstance(specs, list):
        specs = {
            entry["key"]: {name: value for name, value in entry.items() if name != "key"}
            for entry in specs
        }
    return manifest["metrics"], specs


def eval_names(manifest: dict) -> list[str]:
    return [entry.get("name") for entry in manifest.get("evals", []) if isinstance(entry, dict)]


def format_metric(fmt: str, value) -> str:
    try:
        return fmt.format(value)
    except (ValueError, TypeError, IndexError, KeyError):
        return str(value)


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
    specs: dict[str, dict],
    reference_indices: list[int | None] | None = None,
    metric_stats: list[dict] | None = None,
) -> str:
    reference_indices = reference_indices or infer_reference_indices(columns)
    rows = comparison_rows(metrics, specs, reference_indices, metric_stats)
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
    specs: dict[str, dict],
    reference_indices: list[int | None],
    metric_stats: list[dict] | None = None,
) -> list[tuple[str, list[str]]]:
    rows = []
    for key, spec in specs.items():
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            stats = [s.get(key) for s in metric_stats] if metric_stats else []
            formatted = format_metric_values(spec, values, reference_indices, stats)
            rows.append((spec["label"], formatted))
    return rows


def format_metric_values(
    spec: dict,
    values: list,
    reference_indices: list[int | None],
    stats: list[dict | None],
) -> list[str]:
    fmt = spec["format"]
    changes = (
        percent_changes(values, reference_indices)
        if spec.get("change") == "percent"
        else []
    )
    formatted = []
    for index, value in enumerate(values):
        if value is None:
            formatted.append("\u2014")
            continue
        text = format_metric(fmt, value)
        stat = stats[index] if index < len(stats) else None
        if stat is not None and stat.get("count", 0) > 1:
            text = f"{text} [{format_metric_range(stat, fmt)}]"
        change = changes[index] if index < len(changes) else None
        if change is not None:
            text = f"{text} ({format_percent_change(change)})"
        formatted.append(text)
    return formatted


def format_metric_range(stats: dict, fmt: str) -> str:
    return f"{format_metric(fmt, stats['min'])}..{format_metric(fmt, stats['max'])}"


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
    specs: dict[str, dict],
    reference_indices: list[int | None] | None = None,
) -> str:
    reference_indices = reference_indices or infer_reference_indices(columns)
    result = {"variants": columns, "metrics": {}, "metric_changes_pct": {}}
    for key, spec in specs.items():
        values = [m.get(key) for m in metrics]
        if any(v is not None for v in values):
            result["metrics"][key] = values
            if spec.get("change") == "percent" and has_comparisons(reference_indices):
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
            task_data["specs"],
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
                task_data["specs"],
                task_data["reference_indices"],
            )
        )
        task_json["sample_counts"] = task_data["sample_counts"]
        task_json["metric_stats"] = metric_stats_by_key(
            task_data["metric_stats"], task_data["specs"]
        )
        task_json["metric_specs"] = task_data["specs"]
        result["tasks"][task_name] = task_json
    return json.dumps(result, indent=2)


def metric_stats_by_key(
    metric_stats: list[dict], specs: dict[str, dict]
) -> dict[str, list[dict | None]]:
    result = {}
    for key in specs:
        values = [stats.get(key) for stats in metric_stats]
        if any(value is not None for value in values):
            result[key] = values
    return result


def build_task_results(task_entries: list[tuple[str, Path]]) -> dict[str, dict]:
    records = build_compare_records(task_entries)
    return aggregate_records(records)


def aggregate_records(records: list[CompareRecord]) -> dict[str, dict]:
    grouped: dict[str, dict[str, list[dict]]] = {}
    task_specs: dict[str, dict[str, dict]] = {}
    for record in records:
        grouped.setdefault(record.task, {}).setdefault(record.variant, []).append(
            record.metrics
        )
        specs = task_specs.setdefault(record.task, {})
        for key, spec in record.specs.items():
            specs.setdefault(key, spec)

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
            "specs": task_specs[task_name],
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
    from .provenance import digest, load_manifest

    contexts = task_variant_contexts(task_entries)
    collected = []
    seen = set()
    identities: dict[tuple[str, str], set[str]] = {}
    for context, (_label, path) in zip(contexts, task_entries, strict=True):
        manifest = load_manifest(path)
        run_id = manifest.get("run_id")
        if run_id and run_id in seen:
            continue
        if run_id:
            seen.add(run_id)
        identity = digest({key: manifest.get(key) for key in (
            "recipe", "task_source", "prepared", "runtime", "runtime_config", "observed_models", "evals", "host", "harness",
        )}) if manifest else "legacy"
        identities.setdefault(context, set()).add(identity)
        collected.append((context, path, identity, manifest))
    records = []
    for context, path, identity, manifest in collected:
        values, specs = manifest_metrics(manifest, path)
        records.append(CompareRecord(
            task=context[0],
            variant=context[1] + (f"@{identity[:8]}" if len(identities[context]) > 1 else ""),
            metrics=values,
            specs=specs,
        ))
    return records


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
    keys = dict.fromkeys(key for sample in samples for key in sample)
    for key in keys:
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
    metadata = load_task_metadata(path)
    if metadata is not None:
        task_name, variant_name = metadata
        return (
            experiment_root.resolve(), task_name,
            variant_name or implicit_variant_name(config),
        )
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


def load_task_metadata(path: Path) -> tuple[str, str] | None:
    metadata_file = path / ".ccbench-task.json"
    if metadata_file.exists():
        try:
            metadata = json.loads(metadata_file.read_text())
            task_name = metadata["task"]
            variant_name = metadata["variant"]
            if isinstance(task_name, str) and isinstance(variant_name, str):
                return task_name, variant_name
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning(f"Failed to parse task metadata {metadata_file}: {exc}")
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
