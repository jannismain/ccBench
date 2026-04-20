import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from . import paths
from .compare import cmd_compare
from .files import copy_task_shard_first
from .log import log
from .scripts import ensure_project_git_repo, run_scripts_with_env_propagation
from .shards import apply_shard_env, parse_shard_entry, process_shard


def run_experiment(
    experiment: str | None = None,
    *,
    shards: tuple[str, ...] | list[str] = (),
    evals: tuple[str, ...] | list[str] = (),
    variant: str | None = None,
    task: str | None = None,
    skip_run: bool = False,
    results_dir: str | None = None,
) -> Path:
    experiment_name, experiment_config, experiment_file, task_filter = load_experiment(
        experiment,
        shards,
        evals,
        task,
    )
    if variant and experiment_file is None:
        print("Error: --variant can only be used with experiment files.")
        sys.exit(1)

    experiment_root = create_experiment_root(
        experiment_name,
        results_dir,
        experiment_config,
        experiment_file,
    )
    experiment_tasks_root = experiment_root / "tasks"
    experiment_tasks_root.mkdir()

    for task_name in select_tasks(experiment_config, task_filter):
        for variant_name, variant_configs in select_variants(experiment_config, variant):
            run_task_variant(
                experiment_config,
                experiment_name,
                experiment_tasks_root,
                task_name,
                variant_name,
                variant_configs,
                skip_run,
            )

    if not skip_run:
        print()
        cmd_compare([str(experiment_root)], across=False, json_output=False)
    return experiment_root


def load_experiment(
    experiment: str | None,
    shards: tuple[str, ...] | list[str],
    evals: tuple[str, ...] | list[str],
    task: str | None,
) -> tuple[str, dict, Path | None, str | None]:
    if experiment and shards:
        print("Error: pass either an experiment or --shard options, not both.")
        sys.exit(1)
    if experiment and evals:
        print("Error: --eval can only be used with ad-hoc --shard experiments.")
        sys.exit(1)
    if experiment:
        experiment_file = resolve_experiment_file(experiment)
        return experiment_file.stem, load_experiment_config(experiment_file), experiment_file, task

    experiment_config = build_ad_hoc_experiment_config(shards, task, evals)
    return build_ad_hoc_experiment_name(experiment_config), experiment_config, None, None


def build_ad_hoc_experiment_config(
    shards: tuple[str, ...] | list[str],
    task: str | None,
    evals: tuple[str, ...] | list[str] = (),
) -> dict:
    if not task:
        print("Error: ad-hoc experiments require --task.")
        sys.exit(1)
    if not shards:
        print("Error: ad-hoc experiments require at least one --shard.")
        sys.exit(1)
    return {
        "tasks": [task],
        "configs": list(shards),
        "evals": list(evals) or all_available_evals(),
    }


def all_available_evals() -> list[str]:
    if not paths.EVALS.is_dir():
        return []
    return sorted(child.name for child in paths.EVALS.iterdir() if child.is_dir())


def build_ad_hoc_experiment_name(experiment_config: dict) -> str:
    task_name = experiment_config["tasks"][0]
    shard_names = "_".join(str(shard) for shard in experiment_config["configs"])
    return f"adhoc_{task_name}_{shard_names}"


def resolve_experiment_file(experiment: str) -> Path:
    experiment_file = Path(experiment)
    if experiment_file.exists():
        return experiment_file

    experiment_file = paths.EXPERIMENTS / experiment
    if experiment_file.suffix not in {".yaml", ".yml"}:
        experiment_file = experiment_file.with_suffix(".yaml")
    if not experiment_file.exists():
        print(f"Experiment '{experiment_file}' not found.")
        sys.exit(1)
    return experiment_file


def load_experiment_config(experiment_file: Path) -> dict:
    with experiment_file.open() as f:
        return yaml.safe_load(f)


def create_experiment_root(
    experiment_name: str,
    results_dir: str | None,
    experiment_config: dict,
    experiment_file: Path | None = None,
) -> Path:
    result_base = Path(results_dir or os.getenv("CCBENCH_RESULT") or paths.RESULTS)
    timestamp = datetime.now().isoformat(sep="_", timespec="seconds")
    timestamp = timestamp.replace(":", "").replace("-", "")
    experiment_root = result_base / f"{timestamp}_{experiment_name}"
    experiment_root.mkdir(parents=True, exist_ok=True)
    if experiment_file:
        experiment_file.copy_into(experiment_root)
    else:
        experiment_yaml = experiment_root / f"{experiment_name}.yaml"
        experiment_yaml.write_text(yaml.safe_dump(experiment_config, sort_keys=False))
    return experiment_root


def select_tasks(experiment_config: dict, task_filter: str | None) -> list[str]:
    tasks = experiment_config.get("tasks", [])
    if task_filter:
        if task_filter not in tasks:
            print(f"Error: Task '{task_filter}' not found in experiment.")
            print(f"Available tasks: {', '.join(tasks)}")
            sys.exit(1)
        return [task_filter]
    return tasks or ["default"]


def select_variants(
    experiment_config: dict, variant_filter: str | None
) -> list[tuple[str, list]]:
    variants = experiment_config.get("variants") or {}
    if not variants:
        if variant_filter:
            print("Error: --variant specified but experiment has no variants.")
            sys.exit(1)
        return [("", [])]

    all_variants = list(variants.items())
    if not variant_filter:
        return all_variants

    matching_variants = [
        (name, configs) for name, configs in all_variants if name == variant_filter
    ]
    if not matching_variants:
        print(f"Error: Variant '{variant_filter}' not found in experiment.")
        print(f"Available variants: {', '.join([name for name, _ in all_variants])}")
        sys.exit(1)
    return matching_variants


def run_task_variant(
    experiment_config: dict,
    experiment_name: str,
    experiment_tasks_root: Path,
    task_name: str,
    variant_name: str,
    variant_configs: list,
    skip_run: bool,
) -> None:
    task_root = create_task_root(experiment_tasks_root, task_name, variant_name)
    project_dir = task_root / "project"
    project_dir.mkdir()

    env = os.environ.copy()
    env = apply_experiment_env(experiment_config, experiment_name, task_root, env)

    config_shards = experiment_config.get("configs", []) + variant_configs
    task_index = len(config_shards)
    env = assemble_task(task_name, task_root, project_dir, task_index, env)
    env = apply_config_shards(config_shards, task_root, project_dir, env)
    env = apply_eval_shards(
        experiment_config.get("evals", []), task_index, task_root, project_dir, env
    )

    ensure_runnable(task_root)
    ensure_project_git_repo(project_dir)
    if not run_setup_scripts(task_name, task_root, env):
        return
    if skip_run:
        print(f"Prepared task directory at {task_root}, skipping execution.")
        return
    run_task_scripts(task_name, variant_name, task_root, env)


def create_task_root(
    experiment_tasks_root: Path, task_name: str, variant_name: str
) -> Path:
    task_dir_name = f"{task_name}_{variant_name}" if variant_name else task_name
    task_root = experiment_tasks_root / task_dir_name
    task_root.mkdir()
    return task_root


def apply_experiment_env(
    experiment_config: dict, experiment_name: str, task_root: Path, env: dict
) -> dict:
    experiment_env = experiment_config.get("env", {})
    if not experiment_env:
        return env

    for key, value in experiment_env.items():
        env[key] = str(value)
    env_file = task_root / ".env"
    with env_file.open("a") as f:
        f.write(f"\n# === From: experiment ({experiment_name}) ===\n")
        for key, value in experiment_env.items():
            f.write(f"{key}={value}\n")
    return env


def assemble_task(
    task_name: str,
    task_root: Path,
    project_dir: Path,
    task_index: int,
    env: dict,
) -> dict:
    task_dir = paths.TASKS / task_name
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Task '{task_name}' not found at {task_dir}")

    task_copied_first = copy_task_shard_first(
        task_dir,
        project_dir,
        task_root,
        task_index,
        task_name,
    )
    if task_copied_first:
        return env
    return process_shard(task_dir, project_dir, task_root, task_index, task_name, env)


def apply_config_shards(
    config_entries: list, task_root: Path, project_dir: Path, env: dict
) -> dict:
    for index, config_entry in enumerate(config_entries):
        config_shard, shard_env = parse_shard_entry(config_entry)
        shard_dir = paths.FORGE / config_shard
        if not shard_dir.is_dir():
            raise FileNotFoundError(f"Config shard '{config_shard}' not found at {shard_dir}")
        env = process_shard(shard_dir, project_dir, task_root, index, config_shard, env)
        env = apply_shard_env(shard_env, env, task_root, config_shard)
    return env


def apply_eval_shards(
    eval_entries: list,
    task_index: int,
    task_root: Path,
    project_dir: Path,
    env: dict,
) -> dict:
    for index, eval_entry in enumerate(eval_entries, start=task_index + 1):
        eval_shard, shard_env = parse_shard_entry(eval_entry)
        shard_dir = paths.EVALS / eval_shard
        if not shard_dir.is_dir():
            raise FileNotFoundError(f"Eval shard '{eval_shard}' not found at {shard_dir}")
        env = process_shard(shard_dir, project_dir, task_root, index, eval_shard, env)
        env = apply_shard_env(shard_env, env, task_root, eval_shard)
    return env


def ensure_runnable(task_root: Path) -> None:
    if not list(task_root.glob("run.*.sh")):
        sys.exit(f"No run scripts found in '{task_root}'.")


def run_setup_scripts(task_name: str, task_root: Path, env: dict) -> bool:
    success, _env = run_scripts_with_env_propagation(
        "setup.*.sh", task_root, env, stop_on_failure=True
    )
    if not success:
        log.error(f"Setup failed for {task_name}")
    return success


def run_task_scripts(
    task_name: str, variant_name: str, task_root: Path, env: dict
) -> None:
    task_label = f"{task_name} with variant {variant_name}" if variant_name else task_name
    print(f"Running task: {task_label}")
    run_scripts_with_env_propagation("run.*.sh", task_root, env, stop_on_failure=False)
