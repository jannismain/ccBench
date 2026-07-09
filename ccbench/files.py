import json
import shutil
import tomllib
from pathlib import Path
from typing import Callable

import pathspec
import tomli_w

from .log import log
from .paths import (
    CCBENCH_IGNORE,
    DEFAULT_CCBENCH_IGNORE_PATTERNS,
    SCRIPT_NAMES,
    STAGING_SCRIPT,
)


def load_ignore_spec(shard_dir: Path) -> pathspec.PathSpec:
    """Load default ignore patterns plus any shard-local .ccbenchignore rules."""
    ignore_file = shard_dir / CCBENCH_IGNORE
    patterns = list(DEFAULT_CCBENCH_IGNORE_PATTERNS)
    if ignore_file.exists():
        patterns.extend(ignore_file.read_text().splitlines())
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def make_should_skip(shard_dir: Path) -> Callable[[Path], bool]:
    """Build a predicate that skips .ccbenchignore and ignored files."""
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
            result[key] = value
    return result


def append_env_file(source: Path, target: Path, source_name: str) -> None:
    """Append .env file content with a marker showing the source."""
    source_content = source.read_text()
    if not source_content.strip():
        log.info(f"Skipping empty .env file: {source.name} from {source_name}")
        return

    if not target.exists():
        target.write_text("")

    existing_content = target.read_text()
    needs_separator = existing_content and not existing_content.endswith("\n\n")

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
    should_skip: Callable[[Path], bool] | None = None,
) -> None:
    """Copy a file or directory into target_dir."""
    if should_skip and should_skip(source):
        return

    target = target_dir / source.name
    if source.is_dir():
        copy_dir(source, target, merge, source_name, should_skip)
        return

    if not merge:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return

    if source.name in (".env", ".env.sample") and source_name:
        append_env_file(source, target, source_name)
        return

    if merge_structured_file(source, target):
        return

    source.copy_into(target_dir)


def copy_dir(
    source: Path,
    target: Path,
    merge: bool,
    source_name: str | None,
    should_skip: Callable[[Path], bool] | None,
) -> None:
    if merge:
        target.mkdir(exist_ok=True)
        for child in source.iterdir():
            copy_item(child, target, merge, source_name, should_skip)
        return

    ignore = None
    if should_skip:
        skip = should_skip

        def ignore(dirpath, names):
            return [name for name in names if skip(Path(dirpath) / name)]

    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)


def merge_structured_file(source: Path, target: Path) -> bool:
    if not target.exists() or not target.is_file():
        return False
    if source.suffix == ".json":
        return merge_json_file(source, target)
    if source.suffix == ".toml":
        return merge_toml_file(source, target)
    return False


def merge_json_file(source: Path, target: Path) -> bool:
    try:
        with source.open() as f:
            source_data = json.load(f)
        with target.open() as f:
            target_data = json.load(f)
        merged_data = deep_merge_dict(target_data, source_data)
        with target.open("w") as f:
            json.dump(merged_data, f, indent=2)
        log.info(f"Deep merged JSON file: {source.name}")
        return True
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Failed to merge JSON {source.name}: {e}. Falling back to overwrite.")
        return False


def merge_toml_file(source: Path, target: Path) -> bool:
    try:
        with source.open("rb") as f:
            source_data = tomllib.load(f)
        with target.open("rb") as f:
            target_data = tomllib.load(f)
        merged_data = deep_merge_dict(target_data, source_data)
        with target.open("wb") as f:
            tomli_w.dump(merged_data, f)
        log.info(f"Deep merged TOML file: {source.name}")
        return True
    except (tomllib.TOMLDecodeError, KeyError) as e:
        log.warning(f"Failed to merge TOML {source.name}: {e}. Falling back to overwrite.")
        return False


def copy_shard_with_script_rename(
    shard_dir: Path,
    project_dir: Path,
    task_root_dir: Path,
    shard_index: int,
    shard_name: str,
    add_env_markers: bool = True,
    merge_files: bool = True,
) -> None:
    """Copy a shard into project and task-root destinations."""
    should_skip = make_should_skip(shard_dir)
    source_name = shard_name if add_env_markers and merge_files else None

    for item in shard_dir.iterdir():
        if should_skip(item):
            continue
        if item.name == "project" and item.is_dir():
            copy_project_children(
                item, project_dir, shard_index, shard_name, source_name, merge_files
            )
            continue
        copy_or_rename_script(
            item,
            task_root_dir,
            shard_index,
            shard_name,
            source_name,
            should_skip,
            merge_files,
        )


def copy_project_children(
    project_source: Path,
    project_dir: Path,
    shard_index: int,
    shard_name: str,
    source_name: str | None,
    merge_files: bool,
) -> None:
    should_skip = make_should_skip(project_source.parent)
    for child in project_source.iterdir():
        if should_skip(child):
            continue
        copy_or_rename_script(
            child,
            project_dir,
            shard_index,
            shard_name,
            source_name,
            should_skip,
            merge_files,
        )


def copy_or_rename_script(
    item: Path,
    dest_dir: Path,
    shard_index: int,
    shard_name: str,
    source_name: str | None,
    should_skip: Callable[[Path], bool],
    merge_files: bool,
) -> None:
    if item.name in SCRIPT_NAMES:
        prefix = item.stem
        new_name = f"{prefix}.{shard_index:03d}.{shard_name}.sh"
        item.copy_into(dest_dir)
        (dest_dir / item.name).rename(dest_dir / new_name)
        return

    copy_item(
        item,
        dest_dir,
        merge=merge_files,
        source_name=source_name,
        should_skip=should_skip,
    )


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
