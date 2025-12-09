#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pyyaml",
#     "tomli-w",
# ]
# ///

import argparse
import json
import logging
import os
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import tomli_w
import yaml

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


def deep_merge_dict(base: dict, overlay: dict) -> dict:
    """Deep merge overlay dict into base dict, returning a new dict."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge_dict(result[key], value)
            elif isinstance(result[key], list) and isinstance(value, list):
                result[key].extend(value)
            else:
                log.warning(
                    f"Overwriting key '{key}' from {result[key]} to {value} during merge."
                )
                result[key] = value
        else:
            # Key doesn't exist in base, add it from overlay
            result[key] = value
    return result


def copy_file_with_json_merge(source: Path, target_dir: Path) -> None:
    """Copy a file or directory to target directory, deep merging JSON and TOML files."""
    target = target_dir / source.name

    # Handle directories recursively
    if source.is_dir():
        # Create target directory if it doesn't exist
        target.mkdir(exist_ok=True)
        # Recursively copy contents
        for child in source.iterdir():
            copy_file_with_json_merge(child, target)
        return

    # Check if both source and target are JSON files
    if source.suffix == ".json" and target.exists() and target.is_file():
        try:
            # Read both JSON files
            with source.open() as f:
                source_data = json.load(f)
            with target.open() as f:
                target_data = json.load(f)

            # Deep merge: target data is base, source data overlays
            merged_data = deep_merge_dict(target_data, source_data)

            # Write merged result
            with target.open("w") as f:
                json.dump(merged_data, f, indent=2)

            log.info(f"Deep merged JSON file: {source.name}")
            return
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(
                f"Failed to merge JSON {source.name}: {e}. Falling back to overwrite."
            )

    # Check if both source and target are TOML files
    if source.suffix == ".toml" and target.exists() and target.is_file():
        try:
            # Read both TOML files
            with source.open("rb") as f:
                source_data = tomllib.load(f)
            with target.open("rb") as f:
                target_data = tomllib.load(f)

            # Deep merge: target data is base, source data overlays
            merged_data = deep_merge_dict(target_data, source_data)

            # Write merged result
            with target.open("wb") as f:
                tomli_w.dump(merged_data, f)

            log.info(f"Deep merged TOML file: {source.name}")
            return
        except (tomllib.TOMLDecodeError, KeyError) as e:
            log.warning(
                f"Failed to merge TOML {source.name}: {e}. Falling back to overwrite."
            )

    # For non-JSON/TOML files or if merge fails, just copy (overwrite)
    source.copy_into(target_dir)


FORGE = Path(__file__).with_name("config_forge")
TASKS = Path(__file__).with_name("tasks")
ROOT = Path(__file__).with_name("experiments")
EVALS = Path(__file__).with_name("evals")


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run ccBench experiments")
    parser.add_argument("experiment", help="Experiment YAML file (relative to experiments/)")
    parser.add_argument(
        "--variant",
        help="Run only the specified variant (if experiment has variants)",
        default=None,
    )
    args = parser.parse_args()

    # Create experiment directory
    experiment_file = ROOT / args.experiment
    if not experiment_file.exists():
        print(f"Experiment '{experiment_file}' not found.")
        exit(1)
    experiment_name = experiment_file.stem
    experiment_root = (
        ROOT
        / f"{datetime.now().isoformat(sep='_', timespec='seconds').replace(':', '').replace('-', '')}_{experiment_name}"
    )
    experiment_root.mkdir(parents=True, exist_ok=True)
    experiment_file.copy_into(experiment_root)

    with experiment_file.open() as f:
        experiment_config = yaml.safe_load(f)

    experiment_tasks_root = experiment_root / "tasks"
    experiment_tasks_root.mkdir()

    # copy all task files into the project directory
    experiment_task_dirs = []
    for task in experiment_config["tasks"]:
        # Determine what variants to process
        variants_to_process = []
        if "variants" in experiment_config and experiment_config["variants"]:
            all_variants = list(experiment_config["variants"].items())

            # Filter by specified variant if provided
            if args.variant:
                matching_variants = [
                    (name, configs) for name, configs in all_variants if name == args.variant
                ]
                if not matching_variants:
                    print(f"Error: Variant '{args.variant}' not found in experiment.")
                    print(
                        f"Available variants: {', '.join([name for name, _ in all_variants])}"
                    )
                    exit(1)
                variants_to_process = matching_variants
            else:
                variants_to_process = all_variants
        else:
            # No variants, use empty string as variant name
            if args.variant:
                print("Error: --variant specified but experiment has no variants.")
                exit(1)
            variants_to_process = [("", [])]

        for variant_name, variant_configs in variants_to_process:
            # Create task directory with variant suffix if applicable
            if variant_name:
                experiment_task_root = experiment_tasks_root / f"{task}_{variant_name}"
            else:
                experiment_task_root = experiment_tasks_root / task

            experiment_task_root.mkdir()
            experiment_task_dirs.append(experiment_task_root)

            # copy all files of each config shard into the task directory
            project_dir = experiment_task_root / "project"
            project_dir.mkdir()

            # Copy base configs
            for config_shard in experiment_config["configs"]:
                d = Path(FORGE / config_shard)
                for f in d.glob("*"):
                    copy_file_with_json_merge(f, project_dir)

            # Copy variant-specific configs (if any)
            for config_shard in variant_configs:
                d = Path(FORGE / config_shard)
                for f in d.glob("*"):
                    copy_file_with_json_merge(f, project_dir)

            task_dir = TASKS / task
            for f in task_dir.glob("*"):
                f.copy_into(project_dir)

            # move entrypoint and prompt into experiment task root
            entrypoint = project_dir / "run.sh"
            if not entrypoint.exists():
                sys.exit(f"Experiment entrypoint '{entrypoint}' not found.")
            entrypoint.move_into(experiment_task_root)

            prompt_file = project_dir / "prompt.md"
            if not prompt_file.exists():
                sys.exit(f"Experiment prompt file '{prompt_file}' not found.")
            prompt_file.move_into(experiment_task_root)

            os.chdir(experiment_task_root)

            # create INITIAL_FILES manifest
            Path("INITIAL_FILES").write_text(
                "\n".join(
                    [str(f.relative_to(experiment_task_root)) for f in project_dir.glob("*")]
                )
                + "\n"
            )
            os.system("chmod +x run.sh")
            os.system("git init && git add . && git commit -m 'Initial commit'")

            # Run setup script if it exists in the project directory
            setup_script = project_dir / "setup.sh"
            if setup_script.exists():
                print(f"Running setup script for task: {task}")
                os.system(f"chmod +x {setup_script}")
                result = os.system(f"cd {project_dir} && ./setup.sh")
                if result != 0:
                    print(f"Warning: Setup script exited with code {result}")

            # Print task label with variant information
            task_label = f"{task} with variant {variant_name}" if variant_name else task
            print(f"Running task: {task_label}")
            os.system("./run.sh")

            for eval_config in experiment_config["evals"]:
                eval_dir = EVALS / eval_config
                os.system(eval_dir / "run.sh")
