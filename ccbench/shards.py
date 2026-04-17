import shutil
import tempfile
from pathlib import Path

from .files import copy_shard_with_script_rename, make_should_skip
from .log import log
from .paths import STAGING_SCRIPT
from .scripts import run_script_with_env_capture


def process_shard(
    shard_dir: Path,
    project_dir: Path,
    task_root_dir: Path,
    shard_index: int,
    shard_name: str,
    env: dict,
) -> dict:
    """Process a shard and return the possibly updated environment."""
    if not (shard_dir / STAGING_SCRIPT).exists():
        copy_shard_with_script_rename(
            shard_dir, project_dir, task_root_dir, shard_index, shard_name
        )
        return env

    log.info(f"Staging shard '{shard_name}' with {STAGING_SCRIPT} in temporary directory")
    with tempfile.TemporaryDirectory() as stage_dir:
        stage_path = Path(stage_dir)
        copy_shard_to_stage(shard_dir, stage_path)
        env = run_staging_script(stage_path, shard_name, env)
        copy_shard_with_script_rename(
            stage_path,
            project_dir,
            task_root_dir,
            shard_index,
            shard_name,
            add_env_markers=True,
        )
    return env


def copy_shard_to_stage(shard_dir: Path, stage_path: Path) -> None:
    should_skip = make_should_skip(shard_dir)

    def ignore(dirpath: str, names: list[str]) -> list[str]:
        return [name for name in names if should_skip(Path(dirpath) / name)]

    shutil.copytree(shard_dir, stage_path, dirs_exist_ok=True, ignore=ignore)


def run_staging_script(stage_path: Path, shard_name: str, env: dict) -> dict:
    log.info(f"Running {STAGING_SCRIPT} for {shard_name}")
    return_code, env = run_script_with_env_capture(
        stage_path / STAGING_SCRIPT, stage_path, env
    )
    if return_code != 0:
        log.error(f"{STAGING_SCRIPT} for {shard_name} failed with code {return_code}")
        raise RuntimeError(f"{STAGING_SCRIPT} failed for {shard_name}")
    return env


def parse_shard_entry(entry) -> tuple[str, dict]:
    """Parse a shard entry from experiment YAML into (name, env_overrides)."""
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

