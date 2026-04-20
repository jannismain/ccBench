import fnmatch
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from .compare import most_recent_result_dir, resolve_task_dirs
from .log import log
from .scripts import load_script_statuses, run_script_with_env_capture


def retry(
    result_dirs: list[str] | None = None,
    *,
    steps: tuple[str, ...] | list[str] = (),
    tasks: tuple[str, ...] | list[str] = (),
) -> None:
    """Retry failed or selected scripts in existing result directories."""
    resolved_result_dirs = result_dirs or most_recent_result_dir()
    task_entries = resolve_task_dirs(
        resolved_result_dirs,
        across=len(resolved_result_dirs) > 1,
    )
    if not task_entries:
        sys.exit("No task directories found.")

    task_filters = set(tasks)
    retried = 0
    failures = 0
    for label, task_dir in task_entries:
        if task_filters and label not in task_filters and task_dir.name not in task_filters:
            continue
        scripts = select_retry_scripts(task_dir, steps)
        if not scripts:
            print(f"No retryable steps found for {label}.")
            continue
        print(f"Retrying {label}:")
        env = retry_env(task_dir)
        for script in scripts:
            print(f"  {script.name}")
            return_code, env = run_script_with_env_capture(script, task_dir, env)
            retried += 1
            if return_code != 0:
                failures += 1
                log.error(f"Retry step {script.name} failed with code {return_code}")
                if script.name.startswith("setup."):
                    break

    if retried == 0:
        sys.exit("No steps were retried.")
    if failures:
        sys.exit(f"{failures} retried step(s) failed.")


def select_retry_scripts(task_dir: Path, steps: tuple[str, ...] | list[str]) -> list[Path]:
    available_scripts = sorted(task_dir.glob("setup.*.sh")) + sorted(
        task_dir.glob("run.*.sh")
    )
    if steps:
        return scripts_matching_steps(available_scripts, steps)
    return failed_scripts(task_dir, available_scripts)


def scripts_matching_steps(
    available_scripts: list[Path], steps: tuple[str, ...] | list[str]
) -> list[Path]:
    matched = []
    for script in available_scripts:
        if any(step_matches(script, step) for step in steps):
            matched.append(script)
    missing = [
        step
        for step in steps
        if not any(step_matches(script, step) for script in available_scripts)
    ]
    if missing:
        raise FileNotFoundError(f"No scripts matched: {', '.join(missing)}")
    return matched


def step_matches(script: Path, step: str) -> bool:
    return (
        script.name == step
        or str(script) == step
        or fnmatch.fnmatch(script.name, step)
        or fnmatch.fnmatch(str(script), step)
    )


def failed_scripts(task_dir: Path, available_scripts: list[Path]) -> list[Path]:
    statuses = load_script_statuses(task_dir).get("scripts", {})
    failed_names = {
        name
        for name, status in statuses.items()
        if isinstance(status, dict) and status.get("return_code") not in (None, 0)
    }
    return [script for script in available_scripts if script.name in failed_names]


def retry_env(task_dir: Path) -> dict:
    env = os.environ.copy()
    env_file = task_dir / ".env"
    if env_file.exists():
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                env[key] = value
    return env

