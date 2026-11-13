"""Explainable selection of historical samples against a fixed reference run."""

import difflib
import fnmatch
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from . import paths
from .compare import (
    CompareRecord,
    aggregate_records,
    manifest_metrics,
    render_grouped_comparison_json,
    render_grouped_comparison_table,
    resolve_task_dirs,
)
from .provenance import MANIFEST, digest, load_manifest, now


def numeric_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Expected a numeric major.minor.patch version, got {value!r}")
    return tuple(int(part) for part in value.split("."))


def validate_tool_rule(name: str, rule: dict) -> None:
    if not isinstance(rule, dict) or set(rule) - {"min", "max_exclusive", "versions"}:
        raise ValueError(f"Invalid version rule for {name}")
    if not rule or ("versions" in rule and len(rule) != 1):
        raise ValueError(f"Use a version list or numeric bounds for {name}")
    if "versions" in rule:
        if (
            not isinstance(rule["versions"], list)
            or not rule["versions"]
            or not all(isinstance(v, str) for v in rule["versions"])
        ):
            raise ValueError(f"Invalid version list for {name}")
    else:
        for value in rule.values():
            numeric_version(value)
        if (
            "min" in rule
            and "max_exclusive" in rule
            and numeric_version(rule["min"]) >= numeric_version(rule["max_exclusive"])
        ):
            raise ValueError(f"Empty version range for {name}")


def load_policy(path: Path | None) -> dict:
    policy = yaml.safe_load(path.read_text()) if path else {}
    allowed = {
        "tools",
        "prepared_paths",
        "runtime_paths",
        "staged_paths",
        "ignore_fields",
        "max_age_days",
        "states",
    }
    if not isinstance(policy, dict) or set(policy) - allowed:
        raise ValueError(f"Policy must be a mapping with only: {', '.join(sorted(allowed))}")
    for field in ("tools", "staged_paths"):
        if not isinstance(policy.get(field, {}), dict):
            raise ValueError(f"{field} must be a mapping")
    for name, rule in policy.get("tools", {}).items():
        validate_tool_rule(name, rule)
    lists = [
        policy.get("prepared_paths", []),
        policy.get("runtime_paths", []),
        policy.get("ignore_fields", []),
        *policy.get("staged_paths", {}).values(),
    ]
    if any(
        not isinstance(values, list) or not all(isinstance(v, str) for v in values)
        for values in lists
    ):
        raise ValueError("Path and field rules must be lists of strings")
    ignorable = {
        "host.system",
        "host.machine",
        "host.python",
        "harness.revision",
        "harness.source",
    }
    if set(policy.get("ignore_fields", [])) - ignorable:
        raise ValueError(f"ignore_fields may contain only {sorted(ignorable)}")
    if "states" in policy and (
        not isinstance(policy["states"], list)
        or not policy["states"]
        or set(policy["states"]) - {"completed", "completed_with_errors"}
    ):
        raise ValueError("states may contain completed and completed_with_errors")
    age = policy.get("max_age_days")
    if age is not None and (type(age) is not int or age < 1):
        raise ValueError("max_age_days must be a positive integer")
    return policy


def version_allowed(version: str | None, rule: dict) -> bool:
    if version is None:
        return False
    if "versions" in rule:
        return version in rule["versions"]
    try:
        number = numeric_version(version)
    except ValueError:
        return False
    return ("min" not in rule or number >= numeric_version(rule["min"])) and (
        "max_exclusive" not in rule or number < numeric_version(rule["max_exclusive"])
    )


def normalized_files(snapshot: dict) -> dict:
    files = {
        re.sub(r"^(setup|run|post)\.\d+\.", r"\1.", path): entry
        for path, entry in snapshot.get("files", {}).items()
    }
    files.update(
        {f"@env/{path}": value for path, value in snapshot.get("environment", {}).items()}
    )
    return files


def difference(
    field: str, expected, actual, accepted: bool = False, reason: str = "different"
) -> dict:
    return {
        "field": field,
        "expected": expected,
        "actual": actual,
        "accepted": accepted,
        "reason": reason,
    }


def file_differences(
    field: str, expected: dict, actual: dict, allowed: list[str]
) -> list[dict]:
    result = []
    for name in sorted(expected.keys() | actual.keys()):
        if expected.get(name) != actual.get(name):
            accepted = any(fnmatch.fnmatchcase(name, pattern) for pattern in allowed)
            result.append(
                difference(
                    f"{field}/{name}",
                    expected.get(name),
                    actual.get(name),
                    accepted,
                    "declared file difference"
                    if accepted
                    else "file contents or mode differ",
                )
            )
    return result


def recipe_identity(recipe: list) -> list:
    return [
        {"name": entry["name"], "source": entry["source"]["digest"], "env": entry["env"]}
        for entry in recipe
    ]


def intended_paths(reference: dict, candidate: dict) -> list[str]:
    """Paths contributed by differing variant shards are experimental factors."""
    expected = recipe_identity(reference["recipe"])
    actual = recipe_identity(candidate["recipe"])
    result = []
    for manifest, identities, other in (
        (reference, expected, actual),
        (candidate, actual, expected),
    ):
        for index, entry in enumerate(identities):
            if entry in other:
                continue
            for layer in manifest["layers"]:
                if layer["index"] == index and layer["name"] == entry["name"]:
                    result.extend(normalized_files(layer["snapshot"]))
    return result


def compare_layers(reference: dict, candidate: dict, policy: dict) -> list[dict]:
    result = []
    shared = {entry["name"] for entry in reference["recipe"]} & {
        entry["name"] for entry in candidate["recipe"]
    }
    for name in sorted(shared):
        left = [layer for layer in reference["layers"] if layer["name"] == name]
        right = [layer for layer in candidate["layers"] if layer["name"] == name]
        if len(left) != len(right):
            result.append(difference(f"staged/{name}", len(left), len(right)))
            continue
        for first, second in zip(left, right, strict=True):
            result.extend(
                file_differences(
                    f"staged/{name}",
                    normalized_files(first["snapshot"]),
                    normalized_files(second["snapshot"]),
                    policy.get("staged_paths", {}).get(name, []),
                )
            )
    return result


def compare_runtime(reference: dict, candidate: dict, policy: dict) -> list[dict]:
    result = compare_tool_versions(reference, candidate, policy)
    left = reference["runtime"]["env"]
    right = candidate["runtime"]["env"]
    intended_env = intended_env_keys(reference, candidate)
    for key in sorted(left.keys() | right.keys()):
        if left.get(key) != right.get(key):
            result.append(
                difference(
                    f"env.{key}",
                    left.get(key),
                    right.get(key),
                    key in intended_env,
                    "variant override"
                    if key in intended_env
                    else "runtime environment differs",
                )
            )
    return result


def intended_env_keys(reference: dict, candidate: dict) -> set[str]:
    keys = set()
    for entry in reference["recipe"] + candidate["recipe"]:
        if entry not in reference["recipe"] or entry not in candidate["recipe"]:
            keys.update(entry["env"])
    return keys


def compare_tool_versions(reference: dict, candidate: dict, policy: dict) -> list[dict]:
    result = []
    tools = reference["runtime"]["tools"]
    for name, observed in candidate["runtime"]["tools"].items():
        old = observed.get("version")
        new = tools.get(name, {}).get("version")
        rule = policy.get("tools", {}).get(name)
        if rule:
            accepted = version_allowed(old, rule) and (
                new is None or version_allowed(new, rule)
            )
            if old != new or not accepted:
                result.append(
                    difference(
                        f"tools.{name}.version", new, old, accepted, "explicit tool policy"
                    )
                )
        elif old is None or new is None or old != new:
            result.append(
                difference(
                    f"tools.{name}.version",
                    new,
                    old,
                    False,
                    "exact observed versions required",
                )
            )
    for name in policy.get("tools", {}):
        if name not in candidate["runtime"]["tools"]:
            result.append(
                difference(
                    f"tools.{name}.version",
                    "observed version",
                    None,
                    False,
                    "missing tool metadata",
                )
            )
    return result


def execution_differences(reference: dict, candidate: dict, policy: dict) -> list[dict]:
    result = []
    for label, manifest in (("reference", reference), ("candidate", candidate)):
        if manifest["state"] not in policy.get("states", ["completed"]):
            result.append(
                difference(
                    f"{label}.state",
                    "completed",
                    manifest["state"],
                    False,
                    "not an eligible completed execution",
                )
            )
        if (
            manifest.get("runtime_after")
            and manifest["runtime"]["tools"] != manifest["runtime_after"]["tools"]
        ):
            result.append(
                difference(
                    f"{label}.runtime_drift",
                    manifest["runtime"]["tools"],
                    manifest["runtime_after"]["tools"],
                    False,
                    "tools changed during execution",
                )
            )
        for tool, observed in manifest.get("tool_invocations", {}).items():
            expected = manifest["runtime"]["tools"].get(tool, {}).get("version")
            if not observed or any(version != expected for version in observed):
                result.append(
                    difference(
                        f"{label}.invocation_drift.{tool}",
                        expected,
                        observed,
                        False,
                        "invocation version differs from prepared runtime",
                    )
                )
    return result


def prerequisite_differences(
    reference: dict, candidate: dict, baseline: str, policy: dict
) -> list[dict]:
    required = {
        "run_id",
        "recipe",
        "recipes",
        "task_source",
        "task_inputs",
        "prepared",
        "runtime",
        "layers",
        "evals",
        "host",
        "harness",
        "state",
        "metrics",
    }
    if not required <= reference.keys() or not required <= candidate.keys():
        return [
            difference(
                "metadata",
                "complete versioned manifest",
                None,
                False,
                "insufficient metadata",
            )
        ]
    result = execution_differences(reference, candidate, policy)
    expected_recipe = reference["recipes"].get(baseline)
    if expected_recipe is None:
        result.append(
            difference(
                "baseline",
                baseline,
                None,
                False,
                "baseline not declared by reference experiment",
            )
        )
    elif recipe_identity(expected_recipe) != recipe_identity(candidate["recipe"]):
        result.append(
            difference(
                "baseline.recipe",
                recipe_identity(expected_recipe),
                recipe_identity(candidate["recipe"]),
            )
        )
    if policy.get("max_age_days"):
        timestamp = candidate.get("finished_at")
        try:
            age = (
                datetime.now(timezone.utc) - datetime.fromisoformat(str(timestamp))
            ).total_seconds() / 86400
        except ValueError, TypeError:
            age = float("inf")
        if age > policy["max_age_days"]:
            result.append(
                difference(
                    "age_days",
                    policy["max_age_days"],
                    age,
                    False,
                    "too old or missing timestamp",
                )
            )
    return result


def assess_candidate(reference: dict, candidate: dict, baseline: str, policy: dict) -> dict:
    differences = prerequisite_differences(reference, candidate, baseline, policy)
    if any(item["field"] == "metadata" for item in differences):
        return {"eligible": False, "status": "unknown", "differences": differences}
    for field in ("task_source",):
        if reference[field]["digest"] != candidate[field]["digest"]:
            differences.append(
                difference(field, reference[field]["digest"], candidate[field]["digest"])
            )
    if recipe_identity(reference["evals"]) != recipe_identity(candidate["evals"]):
        differences.append(
            difference(
                "evaluators",
                recipe_identity(reference["evals"]),
                recipe_identity(candidate["evals"]),
            )
        )
    for section in ("host", "harness"):
        for key in reference[section].keys() | candidate[section].keys():
            left, right = reference[section].get(key), candidate[section].get(key)
            if left != right:
                field = f"{section}.{key}"
                differences.append(
                    difference(
                        field,
                        left,
                        right,
                        field in policy.get("ignore_fields", []),
                        "environment or harness differs",
                    )
                )
    differences.extend(
        file_differences(
            "task_inputs",
            normalized_files(reference["task_inputs"]),
            normalized_files(candidate["task_inputs"]),
            [],
        )
    )
    differences.extend(compare_layers(reference, candidate, policy))
    differences.extend(compare_runtime(reference, candidate, policy))
    if reference.get("observed_models") != candidate.get("observed_models"):
        intended_model = any(
            "MODEL" in key and reference["runtime"]["env"].get(key) != candidate["runtime"]["env"].get(key)
            for key in intended_env_keys(reference, candidate)
        )
        differences.append(
            difference(
                "observed_models",
                reference.get("observed_models"),
                candidate.get("observed_models"),
                intended_model,
                "declared model variant" if intended_model else "observed model differs",
            )
        )
    allowed_paths = [*intended_paths(reference, candidate), *policy.get("prepared_paths", [])]
    differences.extend(
        file_differences(
            "prepared",
            normalized_files(reference["prepared"]),
            normalized_files(candidate["prepared"]),
            allowed_paths,
        )
    )
    differences.extend(
        file_differences(
            "runtime_config",
            normalized_files(reference.get("runtime_config", {})),
            normalized_files(candidate.get("runtime_config", {})),
            policy.get("runtime_paths", []),
        )
    )
    eligible = all(item["accepted"] for item in differences)
    return {
        "eligible": eligible,
        "status": "compatible"
        if eligible and differences
        else "exact"
        if eligible
        else "incompatible",
        "differences": differences,
    }


def discover_runs(directory: Path) -> list[dict]:
    """Scan portable manifests, deduplicating copies by permanent run ID."""
    found = []
    seen = set()
    for root, dirs, _ in os.walk(directory):
        path = Path(root)
        if (path / MANIFEST).exists() or (path / ".ccbench-task.json").exists():
            manifest = load_manifest(path)
            run_id = manifest.get("run_id")
            if not run_id or run_id not in seen:
                found.append({"path": str(path), "manifest": manifest})
                seen.add(run_id)
            dirs[:] = []
        else:
            dirs[:] = sorted(
                name for name in dirs if name not in {"project", ".git", ".ccbench"}
            )
    return found


def resolve_run(value: str, history_dir: Path) -> Path:
    path = Path(value)
    if path.is_dir():
        if (path / MANIFEST).exists():
            return path
        entries = resolve_task_dirs([str(path)], across=False)
        if len(entries) == 1:
            return entries[0][1]
        raise ValueError(
            "Select an individual task/variant run, or use compare for an experiment directory"
        )
    matches = [
        Path(item["path"])
        for item in discover_runs(history_dir)
        if item["manifest"].get("run_id") == value
    ]
    if len(matches) != 1:
        raise ValueError(f"Run not found: {value}")
    return matches[0]


def default_history_dir() -> Path:
    return Path(os.getenv("CCBENCH_RESULT") or paths.RESULTS)


def find_candidates(
    reference: Path, history_dir: Path, baseline: str, policy: dict
) -> list[dict]:
    manifest = load_manifest(reference)
    if not manifest:
        raise ValueError(f"Reference has no supported manifest: {reference}")
    return [
        {
            "path": record["path"],
            "run_id": record["manifest"].get("run_id"),
            **assess_candidate(manifest, record["manifest"], baseline, policy),
        }
        for record in discover_runs(history_dir)
        if record["manifest"].get("run_id") != manifest["run_id"]
        and (not record["manifest"] or record["manifest"].get("task") == manifest["task"])
    ]


def diff_runs(left: Path, right: Path) -> dict:
    first, second = load_manifest(left), load_manifest(right)
    if not first or not second:
        raise ValueError("Both runs need supported manifests")
    changes = file_differences(
        "prepared",
        normalized_files(first.get("prepared", {})),
        normalized_files(second.get("prepared", {})),
        [],
    )
    for change in changes:
        texts = []
        for root, entry in ((left, change["expected"]), (right, change["actual"])):
            sha = (entry or {}).get("sha256", "")
            path = root / ".ccbench" / "objects" / sha
            texts.append(
                path.read_text(errors="replace").splitlines(keepends=True)
                if re.fullmatch(r"[a-f0-9]{64}", sha)
                and path.is_file()
                and not path.is_symlink()
                else []
            )
        change["diff"] = "".join(difflib.unified_diff(*texts, fromfile="old", tofile="new"))
    return {"left": first, "right": second, "file_changes": changes}


def compare_history(
    result_dirs: list[str],
    baseline: str,
    history_dir: Path,
    policy: dict,
    report: Path | None,
    json_output: bool,
) -> None:
    references = resolve_task_dirs(result_dirs, across=False)
    if not references:
        raise ValueError("No current task results selected")
    selections = select_common_baselines(references, baseline, history_dir, policy)
    records = []
    seen = set()
    manifests = {}
    for (_label, reference), selection in zip(references, selections, strict=True):
        eligible = [item for item in selection["candidates"] if item["selected"]]
        for path in [*(Path(item["path"]) for item in eligible), reference]:
            manifest = load_manifest(path)
            run_id = manifest["run_id"]
            if run_id not in seen:
                seen.add(run_id)
                values, specs = manifest_metrics(manifest, path)
                records.append(
                    CompareRecord(
                        task=manifest["task"],
                        variant=manifest["variant"] if path == reference else baseline,
                        metrics=values,
                        specs=specs,
                    )
                )
                manifests[run_id] = manifest
    grouped = aggregate_records(records)
    comparison = json.loads(render_grouped_comparison_json(grouped))
    saved = {
        "schema_version": 1,
        "created_at": now(),
        "baseline": baseline,
        "policy": policy,
        "policy_digest": digest(policy),
        "run_ids": sorted(seen),
        "runs": manifests,
        "selection": selections,
        "comparison": comparison,
    }
    report = report or history_dir / "comparisons" / f"{uuid4()}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x") as handle:
        json.dump(saved, handle, indent=2, sort_keys=True)
    display_comparison(saved, grouped, json_output)
    if not json_output:
        print(f"Saved comparison: {report}")


def select_common_baselines(references: list[tuple[str, Path]], baseline: str, directory: Path, policy: dict) -> list[dict]:
    selected_ids = {load_manifest(path).get("run_id") for _, path in references}
    selections = []
    common: dict[str, set[str]] = {}
    anchors: dict[tuple[str, str], dict] = {}
    for label, path in references:
        manifest = load_manifest(path)
        key = (manifest.get("task", ""), manifest.get("variant", ""))
        anchor = anchors.setdefault(key, manifest)
        cohort = assess_candidate(anchor, manifest, key[1], policy)
        if not cohort["eligible"]:
            raise ValueError(f"Current runs for {key} do not form a compatible cohort: {cohort['differences']}")
        candidates = [item for item in find_candidates(path, directory, baseline, policy) if item["run_id"] not in selected_ids]
        eligible = {item["run_id"] for item in candidates if item["eligible"]}
        task = manifest["task"]
        common[task] = common.get(task, eligible) & eligible
        if not common[task]:
            raise ValueError(f"No common compatible historical baseline for {label}; use results find to inspect exclusions")
        selections.append({"reference": str(path), "task": task, "candidates": candidates, "reference_compatibility": cohort})
    for selection in selections:
        for candidate in selection["candidates"]:
            candidate["selected"] = candidate["run_id"] in common[selection["task"]]
    return selections


def display_comparison(saved: dict, grouped: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(saved, indent=2))
    else:
        print(render_grouped_comparison_table(grouped))
        for item in saved["selection"]:
            for candidate in item["candidates"]:
                if candidate["selected"]:
                    print(f"Baseline {candidate['run_id']}: {candidate['status']}")
                    for change in candidate["differences"]:
                        print(
                            f"  Accepted {change['field']}: {change['expected']} -> {change['actual']} ({change['reason']})"
                        )
