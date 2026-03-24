#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pyyaml",
#     "tomli-w",
#     "coloredlogs",
#     "pathspec",
#     "python-dotenv",
# ]
# ///

import argparse
import fcntl
import json
import logging
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import tomllib
import tty
from datetime import datetime
from pathlib import Path

import coloredlogs
import pathspec
import tomli_w
import yaml
from dotenv import load_dotenv

log = logging.getLogger("ccBench")
coloredlogs.install(
    level=os.getenv("CCBENCH_LOG_LEVEL", "INFO"),
    fmt="%(asctime)s %(name)s %(levelname)-6s %(message)s (%(filename)s:%(lineno)d)",
    logger=log,
)
log.propagate = False  # Avoid double logging if root logger is configured elsewhere


CCBENCH_DIR = Path(__file__).resolve().parent


# Script names that should be renamed with index prefix
STAGING_SCRIPT = "staging.sh"
SCRIPT_NAMES = {STAGING_SCRIPT, "setup.sh", "run.sh"}

CCBENCH_IGNORE = ".ccbenchignore"
DEFAULT_CCBENCH_IGNORE_PATTERNS = ("__pycache__", ".npm")


def load_ignore_spec(shard_dir: Path):
    """Load default ignore patterns plus any shard-local .ccbenchignore rules."""
    ignore_file = shard_dir / CCBENCH_IGNORE
    patterns = list(DEFAULT_CCBENCH_IGNORE_PATTERNS)
    if ignore_file.exists():
        patterns.extend(ignore_file.read_text().splitlines())
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _make_should_skip(shard_dir: Path):
    """Build a predicate that skips .ccbenchignore and files matching ignore patterns."""
    ignore_spec = load_ignore_spec(shard_dir)

    def should_skip(item: Path) -> bool:
        if item.name == CCBENCH_IGNORE:
            return True
        try:
            rel = item.relative_to(shard_dir)
        except ValueError:
            return False
        return bool(ignore_spec.match_file(rel))

    return should_skip


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


def append_env_file(source: Path, target: Path, source_name: str) -> None:
    """Append .env file content with a marker showing the source."""
    source_content = source.read_text()

    # Skip if source is empty
    if not source_content.strip():
        log.info(f"Skipping empty .env file: {source.name} from {source_name}")
        return

    # Create target if it doesn't exist
    if not target.exists():
        target.write_text("")

    # Read existing content to check if we need a newline separator
    existing_content = target.read_text()
    needs_separator = existing_content and not existing_content.endswith("\n\n")

    # Append with source marker
    with target.open("a") as f:
        if existing_content and needs_separator:
            f.write("\n")
        f.write(f"# === From: {source_name} ===\n")
        f.write(source_content)
        if not source_content.endswith("\n"):
            f.write("\n")

    log.info(f"Appended .env file: {source.name} from {source_name}")


def copy_item(
    source: Path,
    target_dir: Path,
    merge: bool = True,
    source_name: str | None = None,
    should_skip=None,
) -> None:
    """Copy a file or directory into target_dir.

    When merge=True, JSON/TOML files are deep-merged with existing targets,
    .env files are appended with source markers, and directories are walked
    recursively. When merge=False, files are copied directly.
    """
    if should_skip and should_skip(source):
        return

    target = target_dir / source.name

    if source.is_dir():
        if merge:
            target.mkdir(exist_ok=True)
            for child in source.iterdir():
                copy_item(child, target, merge, source_name, should_skip)
        else:
            ignore = None
            if should_skip:

                def ignore(dirpath, names):
                    return [n for n in names if should_skip(Path(dirpath) / n)]

            shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
        return

    if not merge:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return

    # Handle .env and .env.sample files specially - append with source marker
    if source.name in (".env", ".env.sample") and source_name:
        append_env_file(source, target, source_name)
        return

    # Merge JSON files
    if source.suffix == ".json" and target.exists() and target.is_file():
        try:
            with source.open() as f:
                source_data = json.load(f)
            with target.open() as f:
                target_data = json.load(f)
            merged_data = deep_merge_dict(target_data, source_data)
            with target.open("w") as f:
                json.dump(merged_data, f, indent=2)
            log.info(f"Deep merged JSON file: {source.name}")
            return
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(
                f"Failed to merge JSON {source.name}: {e}. Falling back to overwrite."
            )

    # Merge TOML files
    if source.suffix == ".toml" and target.exists() and target.is_file():
        try:
            with source.open("rb") as f:
                source_data = tomllib.load(f)
            with target.open("rb") as f:
                target_data = tomllib.load(f)
            merged_data = deep_merge_dict(target_data, source_data)
            with target.open("wb") as f:
                tomli_w.dump(merged_data, f)
            log.info(f"Deep merged TOML file: {source.name}")
            return
        except (tomllib.TOMLDecodeError, KeyError) as e:
            log.warning(
                f"Failed to merge TOML {source.name}: {e}. Falling back to overwrite."
            )

    # Fallback: just copy (overwrite)
    source.copy_into(target_dir)


def copy_shard_with_script_rename(
    shard_dir: Path,
    project_dir: Path,
    task_root_dir: Path,
    shard_index: int,
    shard_name: str,
    add_env_markers: bool = True,
    merge_files: bool = True,
) -> None:
    """Copy a shard, routing files to project_dir or task_root_dir.

    Files inside a ``project/`` subdirectory in the shard are copied into
    *project_dir* (the agent workspace). Everything else goes to
    *task_root_dir* (ccBench infrastructure).
    """
    should_skip = _make_should_skip(shard_dir)
    source_name = shard_name if add_env_markers and merge_files else None

    def _copy_or_rename(item: Path, dest_dir: Path) -> None:
        if item.name in SCRIPT_NAMES:
            prefix = item.stem
            new_name = f"{prefix}.{shard_index:03d}.{shard_name}.sh"
            item.copy_into(dest_dir)
            (dest_dir / item.name).rename(dest_dir / new_name)
        else:
            copy_item(
                item,
                dest_dir,
                merge=merge_files,
                source_name=source_name,
                should_skip=should_skip,
            )

    for item in shard_dir.iterdir():
        if should_skip(item):
            continue
        if item.name == "project" and item.is_dir():
            for child in item.iterdir():
                if should_skip(child):
                    continue
                _copy_or_rename(child, project_dir)
            continue
        _copy_or_rename(item, task_root_dir)


def copy_task_shard_first(
    task_dir: Path,
    project_dir: Path,
    task_root_dir: Path,
    shard_index: int,
    task_name: str,
) -> bool:
    """Fast-copy a task shard before overlay shards when no staging is required."""
    if (task_dir / STAGING_SCRIPT).exists():
        log.info(
            f"Task '{task_name}' uses {STAGING_SCRIPT}; deferring to merge-aware task processing."
        )
        return False

    copy_shard_with_script_rename(
        task_dir,
        project_dir,
        task_root_dir,
        shard_index,
        task_name,
        add_env_markers=False,
        merge_files=False,
    )
    return True


def run_script_with_env_capture(
    script: Path, working_dir: Path, env: dict
) -> tuple[int, dict]:
    """
    Run a script with interactive terminal support while capturing output and env.

    Uses a pseudo-terminal (pty) to maintain interactivity for prompts like `read -p`.
    Returns (return_code, updated_env).
    """
    log_file = script.with_suffix(".log")
    env_file = working_dir / ".env_capture"

    # Make script executable
    script.chmod(script.stat().st_mode | 0o755)

    # Wrapper script that:
    # 1. Sources the actual script (so env changes persist)
    # 2. Captures exit code
    # 3. Dumps environment to file
    wrapper = f"""
source {script.name}
__exit_code=$?
env > {env_file}
exit $__exit_code
"""

    # Create pseudo-terminal for interactive support
    master_fd, slave_fd = pty.openpty()

    # Set PTY window size so child processes see valid terminal dimensions.
    # Without this, tools like @clack/prompts get process.stdout.columns == 0
    # and crash with "Invalid count value" when computing padding.
    winsize = struct.pack("HHHH", 40, 120, 0, 0)  # rows, cols, xpixel, ypixel
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    # Check if stdin is a real terminal (not redirected/piped)
    stdin_is_tty = sys.stdin.isatty()
    old_settings = None

    try:
        # If stdin is a tty, set it to raw mode for interactive input
        if stdin_is_tty:
            stdin_fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)

        # Start process with pty
        process = subprocess.Popen(
            ["bash", "-c", wrapper],
            cwd=working_dir,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
        )

        # Close slave fd in parent process (child has its own copy)
        os.close(slave_fd)

        # Bidirectional communication: stdin → pty and pty → stdout
        with open(log_file, "wb") as log_f:
            while True:
                # Monitor stdin (if tty) and master_fd
                if stdin_is_tty:
                    ready_read, _, _ = select.select([sys.stdin, master_fd], [], [], 0.1)
                else:
                    ready_read, _, _ = select.select([master_fd], [], [], 0.1)

                # Forward user input to script (only if stdin is a tty)
                if stdin_is_tty and sys.stdin in ready_read:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if data:
                            # Check for Ctrl+C (0x03) and send SIGINT to process
                            if b"\x03" in data:
                                process.send_signal(signal.SIGINT)
                            else:
                                os.write(master_fd, data)
                    except OSError:
                        pass

                # Forward script output to terminal and log
                if master_fd in ready_read:
                    try:
                        data = os.read(master_fd, 1024)
                        if not data:
                            break
                        # Write to terminal
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        # Write to log file
                        log_f.write(data)
                    except OSError:
                        break

                # Check if process has finished
                if process.poll() is not None:
                    # Read any remaining output
                    try:
                        while True:
                            data = os.read(master_fd, 1024)
                            if not data:
                                break
                            sys.stdout.buffer.write(data)
                            sys.stdout.buffer.flush()
                            log_f.write(data)
                    except OSError:
                        pass
                    break

        process.wait()
        return_code = process.returncode

    finally:
        # Restore original terminal settings if we changed them
        if old_settings is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
        # Clean up master fd
        os.close(master_fd)

    # Parse captured environment
    new_env = env.copy()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                new_env[key] = value
        env_file.unlink()  # Clean up

    return return_code, new_env


def run_scripts_with_env_propagation(
    script_pattern: str, working_dir: Path, env: dict, stop_on_failure: bool = True
) -> tuple[bool, dict]:
    """
    Run scripts matching pattern in alphabetical order, propagating environment.

    Returns (success, final_env).
    """
    scripts = sorted(working_dir.glob(script_pattern))
    all_succeeded = True

    for script in scripts:
        log.info(f"Running {script.name}")

        return_code, env = run_script_with_env_capture(script, working_dir, env)

        if return_code != 0:
            log.error(f"Script {script.name} failed with code {return_code}")
            all_succeeded = False
            if stop_on_failure:
                return False, env
            # Continue on failure for run scripts

    return all_succeeded, env


def ensure_project_git_repo(project_dir: Path) -> None:
    """Ensure the agent workspace has its own git repo without mutating imported repos."""
    project_root = project_dir.resolve()
    existing_repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if existing_repo.returncode == 0:
        repo_root = Path(existing_repo.stdout.strip()).resolve()
        if repo_root == project_root:
            log.info(f"Project directory already has a git repo at {project_dir}")
            return

    commands = (
        ["git", "init", "--quiet"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=ccBench",
            "-c",
            "user.email=ccbench@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "before experiment",
        ],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to initialize git repo in {project_dir}: "
                f"{' '.join(command)}\n{result.stderr.strip()}"
            )


def process_shard(
    shard_dir: Path,
    project_dir: Path,
    task_root_dir: Path,
    shard_index: int,
    shard_name: str,
    env: dict,
) -> dict:
    """Process a shard: run staging script if exists, then copy with renaming.

    Returns the (possibly updated) environment dict.
    """
    if (shard_dir / STAGING_SCRIPT).exists():
        # Stage shard to temp dir
        log.info(f"Staging shard '{shard_name}' with {STAGING_SCRIPT} in temporary directory")
        with tempfile.TemporaryDirectory() as stage_dir:
            stage_path = Path(stage_dir)
            # Copy all shard files to staging (no markers - staging is isolated)
            should_skip = _make_should_skip(shard_dir)

            def ignore(dirpath: str, names: list[str]) -> list[str]:
                return [n for n in names if should_skip(Path(dirpath) / n)]

            shutil.copytree(
                shard_dir,
                stage_path,
                dirs_exist_ok=True,
                ignore=ignore,
            )

            # Run staging script in staging dir
            log.info(f"Running {STAGING_SCRIPT} for {shard_name}")
            return_code, env = run_script_with_env_capture(
                stage_path / STAGING_SCRIPT, stage_path, env
            )
            if return_code != 0:
                log.error(f"{STAGING_SCRIPT} for {shard_name} failed with code {return_code}")
                raise RuntimeError(f"{STAGING_SCRIPT} failed for {shard_name}")

            # Now copy from staging to target with script renaming and markers
            copy_shard_with_script_rename(
                stage_path,
                project_dir,
                task_root_dir,
                shard_index,
                shard_name,
                add_env_markers=True,
            )
    else:
        # No staging script, copy directly with renaming
        copy_shard_with_script_rename(
            shard_dir, project_dir, task_root_dir, shard_index, shard_name
        )

    return env


def parse_shard_entry(entry) -> tuple[str, dict]:
    """Parse a shard entry from experiment YAML into (name, env_overrides).

    Entries can be a plain string or a dict with env overrides:
      - "claude_code" -> ("claude_code", {})
      - {"openspec": {"env": {"TOOLS": "claude"}}} -> ("openspec", {"TOOLS": "claude"})
    """
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        if len(entry) != 1:
            raise ValueError(f"Shard entry dict must have exactly one key, got: {entry}")
        name = next(iter(entry))
        props = entry[name] or {}
        return name, {k: str(v) for k, v in props.get("env", {}).items()}
    raise TypeError(f"Shard entry must be a string or dict, got: {type(entry)}")


def apply_shard_env(shard_env: dict, env: dict, task_root_dir: Path, shard_name: str) -> dict:
    """Apply per-shard env overrides to runtime env and .env file."""
    if not shard_env:
        return env
    for key, value in shard_env.items():
        env[key] = value
    env_file = task_root_dir / ".env"
    with env_file.open("a") as f:
        f.write(f"\n# === From: {shard_name} (experiment override) ===\n")
        for key, value in shard_env.items():
            f.write(f"{key}={value}\n")
    return env


FORGE = Path(__file__).with_name("config_forge")
TASKS = Path(__file__).with_name("tasks")
EXPERIMENTS = Path(__file__).with_name("experiments")
RESULTS = Path(__file__).with_name("results")
EVALS = Path(__file__).with_name("evals")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(".env")

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run ccBench experiments")
    parser.add_argument("experiment", help="Experiment YAML file (relative to experiments/)")
    parser.add_argument(
        "--variant",
        help="Run only the specified variant (if experiment has variants)",
        default=None,
    )
    parser.add_argument(
        "--task",
        help="Run only the specified task from the experiment",
        default=None,
    )
    parser.add_argument(
        "--skip-run",
        help="Prepare experiment directories but skip execution",
        default=False,
        action="store_true",
    )
    parser.add_argument(
        "--results-dir",
        help="Directory for experiment results (default: $CCBENCH_RESULT or ./results)",
        default=None,
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir or os.getenv("CCBENCH_RESULT") or RESULTS)

    # Create experiment directory
    experiment_file = Path(args.experiment)
    if not Path(experiment_file).exists():
        # assume experiment is only referenced by name
        experiment_file = EXPERIMENTS / args.experiment
        if experiment_file.suffix not in {".yaml", ".yml"}:
            experiment_file = experiment_file.with_suffix(".yaml")
        if not experiment_file.exists():
            print(f"Experiment '{experiment_file}' not found.")
            exit(1)
    experiment_name = experiment_file.stem
    experiment_root = (
        results_dir
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
    tasks = experiment_config["tasks"]
    if args.task:
        if args.task not in tasks:
            print(f"Error: Task '{args.task}' not found in experiment.")
            print(f"Available tasks: {', '.join(tasks)}")
            exit(1)
        tasks = [args.task]
    for task in tasks:
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

            # Initialize environment for script execution
            env = os.environ.copy()

            # Apply experiment-level env overrides early so shard-level
            # env entries (set per-shard in the YAML) can override them
            # in both the runtime env dict and the .env file.
            experiment_env = experiment_config.get("env", {})
            if experiment_env:
                for key, value in experiment_env.items():
                    env[key] = str(value)
                env_file = experiment_task_root / ".env"
                with env_file.open("a") as f:
                    f.write(f"\n# === From: experiment ({experiment_name}) ===\n")
                    for key, value in experiment_env.items():
                        f.write(f"{key}={value}\n")

            all_config_shards = experiment_config.get("configs", []) + variant_configs
            task_index = len(all_config_shards)

            task_dir = TASKS / task
            if not task_dir.is_dir():
                raise FileNotFoundError(f"Task '{task}' not found at {task_dir}")

            # Copy the task source first so large project trees avoid merge checks.
            task_copied_first = copy_task_shard_first(
                task_dir,
                project_dir,
                experiment_task_root,
                task_index,
                task,
            )
            # 1. Process task shard
            if not task_copied_first:
                env = process_shard(
                    task_dir,
                    project_dir,
                    experiment_task_root,
                    task_index,
                    task,
                    env,
                )

            # 2. Process config shards (base + variant-specific)
            for index, config_entry in enumerate(all_config_shards):
                config_shard, shard_env = parse_shard_entry(config_entry)
                shard_dir = FORGE / config_shard
                if not shard_dir.is_dir():
                    raise FileNotFoundError(
                        f"Config shard '{config_shard}' not found at {shard_dir}"
                    )
                env = process_shard(
                    shard_dir,
                    project_dir,
                    experiment_task_root,
                    index,
                    config_shard,
                    env,
                )
                env = apply_shard_env(shard_env, env, experiment_task_root, config_shard)

            # 3. Process eval shards
            for index, eval_entry in enumerate(
                experiment_config.get("evals", []), start=task_index + 1
            ):
                eval_shard, shard_env = parse_shard_entry(eval_entry)
                shard_dir = EVALS / eval_shard
                if not shard_dir.is_dir():
                    raise FileNotFoundError(
                        f"Eval shard '{eval_shard}' not found at {shard_dir}"
                    )
                env = process_shard(
                    shard_dir,
                    project_dir,
                    experiment_task_root,
                    index,
                    eval_shard,
                    env,
                )
                env = apply_shard_env(shard_env, env, experiment_task_root, eval_shard)

            # Verify run scripts exist at task root
            run_scripts = list(experiment_task_root.glob("run.*.sh"))
            if not run_scripts:
                sys.exit(f"No run scripts found in '{experiment_task_root}'.")

            # Initialize git repository in the agent workspace.
            ensure_project_git_repo(project_dir)

            # Run all setup scripts in order (config -> task -> eval)
            success, env = run_scripts_with_env_propagation(
                "setup.*.sh", experiment_task_root, env, stop_on_failure=True
            )

            if not success:
                log.error(f"Setup failed for {task}")
                continue

            if args.skip_run:
                print(
                    f"Prepared task directory at {experiment_task_root}, skipping execution."
                )
                continue

            # Print task label with variant information
            task_label = f"{task} with variant {variant_name}" if variant_name else task
            print(f"Running task: {task_label}")

            success, env = run_scripts_with_env_propagation(
                "run.*.sh", experiment_task_root, env, stop_on_failure=False
            )
