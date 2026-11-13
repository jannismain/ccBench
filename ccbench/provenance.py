"""Versioned, portable evidence about the inputs to a single execution."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from uuid import uuid4

import yaml
from dotenv import dotenv_values

from . import paths
from .files import make_should_skip

MANIFEST = ".ccbench/manifest.json"
SECRET_KEY = re.compile(r"secret|password|token|api[_-]?key|auth|credential", re.I)
VERSION = re.compile(r"(?<![\d.])(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)")
EXCLUDED_DIRS = {
    ".git",
    ".ccbench",
    ".claude-runtime",
    "node_modules",
    ".venv",
    "__pycache__",
    ".npm",
}
TRACKED_ENV = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "OPENAI_MODEL",
    "OPENAI_BASE_URL",
    "AGENT_PROMPT",
    "AGENT",
    "BROWNFIELD",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def secret_values(env: dict) -> list[str]:
    return [str(value) for key, value in env.items() if SECRET_KEY.search(key) and value]


def redact(text: str, env: dict) -> str:
    for value in sorted(secret_values(env), key=len, reverse=True):
        text = str(text).replace(str(value), "<redacted>")
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1<redacted>@", text)
    return text


def safe_env(env: dict) -> dict:
    return {
        str(key): redact(str(value), env)
        for key, value in env.items()
        if not SECRET_KEY.search(str(key)) and not str(key).startswith("CCBENCH_")
    }


def excluded_file(path: Path) -> bool:
    return (
        path.name.startswith(".env")
        or SECRET_KEY.search(path.name) is not None
        or path.suffix in {".log", ".pem", ".key"}
        or path.name.startswith(".ccbench")
        or path.name == "specify-version.txt"
    )


def snapshot_entry(path: Path, objects: Path, env: dict) -> dict:
    if path.is_symlink():
        return {"type": "symlink", "target": redact(os.readlink(path), env)}
    content = path.read_bytes()
    # Do not persist files containing credentials, including unrecognized local credentials.
    text = content.decode("utf-8", errors="replace")
    if any(value in text for value in secret_values(env)) or re.search(
        r"""(?i)(?:api[_-]?key|auth[_-]?token|password|secret)["']?\s*[:=]\s*["']?[^\s"'${}]{8,}""",
        text,
    ):
        return {"type": "excluded", "reason": "sensitive content"}
    sha = hashlib.sha256(content).hexdigest()
    objects.mkdir(parents=True, exist_ok=True)
    try:
        with (objects / sha).open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        pass
    return {"type": "file", "sha256": sha, "executable": bool(path.stat().st_mode & 0o111)}


def snapshot_tree(root: Path, objects: Path, env: dict) -> dict:
    """Snapshot relevant files without following symlinks or storing credentials."""
    files: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    environment: dict[str, dict] = {}
    skip = make_should_skip(root)
    if not root.is_dir():
        return {"files": {}, "excluded": {}, "digest": None, "missing": True}
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            child = parent / name
            if child.is_symlink():
                dirs.remove(name)
                names.append(name)
            elif name in EXCLUDED_DIRS or skip(child):
                dirs.remove(name)
                excluded[child.relative_to(root).as_posix()] = "excluded directory"
        for name in sorted(names):
            child = parent / name
            rel = child.relative_to(root).as_posix()
            if child.name == ".env" and not child.is_symlink():
                environment[rel] = safe_env(
                    {
                        key: value
                        for key, value in dotenv_values(child, interpolate=False).items()
                        if value is not None
                    }
                )
            if excluded_file(child) or skip(child):
                excluded[rel] = "excluded file"
                continue
            files[rel] = snapshot_entry(child, objects, env)
    return {
        "files": files,
        "environment": environment,
        "excluded": excluded,
        "digest": digest({"files": files, "environment": environment}),
    }


def write_manifest(root: Path, manifest: dict) -> None:
    target = root / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    temporary.replace(target)


def load_manifest(root: Path) -> dict:
    try:
        value = json.loads((root / MANIFEST).read_text())
    except OSError, json.JSONDecodeError:
        return {}
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return {}
    if not isinstance(value.get("run_id"), str):
        return {}
    for key in (
        "recipes",
        "task_source",
        "task_inputs",
        "prepared",
        "runtime",
        "runtime_after",
        "metrics",
        "host",
        "harness",
    ):
        if key in value and not isinstance(value[key], dict):
            return {}
    for key in ("recipe", "evals", "layers"):
        if key in value and not isinstance(value[key], list):
            return {}
    return value


def recipe_entry(entry, directory: Path, objects: Path, env: dict) -> dict:
    if isinstance(entry, str):
        name, properties = entry, {}
    else:
        name = next(iter(entry))
        properties = entry[name] or {}
    snapshot = snapshot_tree(directory / name, objects, env)
    return {"name": name, "source": snapshot, "env": safe_env(properties.get("env") or {})}


def git_revision(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def initialize_manifest(
    root: Path, config: dict, task: str, variant: str, env: dict, parallel_jobs: int = 1
) -> None:
    objects = root / ".ccbench" / "objects"
    base = config.get("configs", [])
    variants = config.get("variants") or {"": []}
    recipes = {
        name: [recipe_entry(entry, paths.FORGE, objects, env) for entry in [*base, *entries]]
        for name, entries in variants.items()
    }
    manifest = {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "created_at": now(),
        "state": "preparing",
        "task": task,
        "variant": variant,
        "recipe": recipes[variant],
        "recipes": recipes,
        "task_source": snapshot_tree(paths.TASKS / task, objects, env),
        "evals": [
            recipe_entry(entry, paths.EVALS, objects, env)
            for entry in config.get("evals", [])
        ],
        "experiment_env": safe_env(config.get("env") or {}),
        "layers": [],
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "execution": {
            "parallel_jobs": parallel_jobs,
            "os_release": platform.release(),
            "cpu_count": os.cpu_count(),
        },
        "harness": {
            "revision": git_revision(paths.CCBENCH_DIR),
            "source": snapshot_tree(paths.CCBENCH_DIR / "ccbench", objects, env)["digest"],
        },
        "coverage": {
            "environment": "declared variables and built-in allowlist",
            "external_services": "not reproducible from local metadata",
            "snapshot_exclusions_version": 1,
        },
    }
    write_manifest(root, manifest)


def record_layer(root: Path, source: Path, name: str, index: int, env: dict) -> None:
    manifest = load_manifest(root)
    if not manifest:
        return
    manifest["layers"].append(
        {
            "name": name,
            "index": index,
            "snapshot": snapshot_tree(source, root / ".ccbench" / "objects", env),
        }
    )
    write_manifest(root, manifest)


def record_task_inputs(root: Path, env: dict) -> None:
    manifest = load_manifest(root)
    manifest["task_inputs"] = snapshot_tree(root, root / ".ccbench" / "objects", env)
    manifest["project_revision"] = git_revision(root / "project")
    write_manifest(root, manifest)


def probe_tool(command: list[str], root: Path, env: dict) -> dict:
    argv = [Template(arg).safe_substitute(env) for arg in command]
    executable = shutil.which(argv[0], path=env.get("PATH"))
    if not executable:
        return {"version": None, "status": "unavailable"}
    try:
        result = subprocess.run(
            [executable, *argv[1:]],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = VERSION.search(result.stdout + result.stderr)
        return {
            "version": match.group(1) if match and result.returncode == 0 else None,
            "executable": redact(str(Path(executable).resolve()), env),
            "status": "observed" if match and result.returncode == 0 else "unknown",
        }
    except OSError, subprocess.TimeoutExpired:
        return {"version": None, "status": "unavailable"}


def shard_metadata(root: Path, entry: dict) -> dict:
    metadata = entry["source"]["files"].get("metadata.yaml", {})
    sha = metadata.get("sha256")
    if not sha:
        return {}
    value = yaml.safe_load((root / ".ccbench" / "objects" / sha).read_text()) or {}
    if not isinstance(value, dict) or set(value) - {"tools", "env"}:
        raise ValueError(f"Invalid metadata.yaml for {entry['name']}")
    return value


def tool_declarations(root: Path, manifest: dict) -> dict:
    declarations = {}
    for entry in manifest["recipe"]:
        config = shard_metadata(root, entry)
        if config:
            for name, spec in config.get("tools", {}).items():
                command = spec.get("command")
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(arg, str) for arg in command)
                ):
                    raise ValueError(f"Invalid tool command for {name} in {entry['name']}")
                declarations[name] = command
    return declarations


def collect_runtime(root: Path, env: dict, manifest: dict) -> dict:
    tools = {
        name: probe_tool(command, root, env)
        for name, command in tool_declarations(root, manifest).items()
    }
    names = {entry["name"] for entry in manifest["recipe"]}
    if names & {"claude_code", "speckit"}:
        executable = env.get("CLAUDE_BIN") if "speckit" in names else env.get("CLAUDE")
        tools["claude_code"] = probe_tool(
            [executable or str(Path.home() / ".local/bin/claude"), "--version"], root, env
        )
    if "speckit" in names:
        # Captured from the actual staged invocation; never resolve/install uvx here.
        tools["specify"] = {"version": None, "status": "unknown"}
        for log in root.glob("staging.*.speckit.log"):
            match = re.search(
                r"(?i)(?:specify|spec.?kit|cli version)[^\n]*?" + VERSION.pattern,
                log.read_text(errors="replace"),
            )
            if match:
                tools["specify"] = {
                    "version": match.group(1),
                    "status": "observed",
                    "source": "staging log",
                }
        version_file = root / "specify-version.txt"
        if version_file.is_file():
            match = re.search(
                r"(?i)(?:specify|spec.?kit|cli version)[^\n]*?" + VERSION.pattern,
                version_file.read_text(errors="replace"),
            )
            if match:
                tools["specify"] = {
                    "version": match.group(1),
                    "status": "observed",
                    "source": "staging version probe (unpinned source)",
                }
    declared = set(manifest["experiment_env"])
    for entry in manifest["recipe"]:
        declared.update(entry["env"])
        metadata_env = shard_metadata(root, entry).get("env", [])
        if not isinstance(metadata_env, list) or not all(
            isinstance(key, str) for key in metadata_env
        ):
            raise ValueError(
                f"metadata.env must be a list of variable names for {entry['name']}"
            )
        declared.update(metadata_env)
    tracked = {key: env[key] for key in set(TRACKED_ENV) | declared if key in env}
    return {"tools": tools, "env": safe_env(tracked)}


def record_prepared(root: Path, env: dict) -> None:
    manifest = load_manifest(root)
    manifest["prepared"] = snapshot_tree(root, root / ".ccbench" / "objects", env)
    manifest["runtime"] = collect_runtime(root, env, manifest)
    manifest["runtime_config"] = snapshot_tree(
        root / ".claude-runtime", root / ".ccbench" / "objects", env
    )
    manifest["started_at"] = now()
    manifest["state"] = "prepared"
    write_manifest(root, manifest)


def finish_manifest(root: Path, state: str, env: dict) -> None:
    from .compare import eval_names, load_metrics
    from .scripts import load_script_statuses

    manifest = load_manifest(root)
    manifest["state"] = state
    manifest["finished_at"] = now()
    manifest["scripts"] = load_script_statuses(root).get("scripts", {})
    if state == "completed" and any(
        item.get("return_code") != 0 for item in manifest["scripts"].values()
    ):
        manifest["state"] = "completed_with_errors"
    manifest["metrics"], specs = load_metrics(root, eval_names(manifest))
    # A list keeps the evals' row order; the manifest is written with sorted keys.
    manifest["metric_specs"] = [{"key": key, **spec} for key, spec in specs.items()]
    manifest["observed_models"] = observed_models(root)
    manifest["runtime_after"] = collect_runtime(root, env, manifest)
    observations = root / ".ccbench" / "claude_code.versions"
    if observations.exists():
        manifest["tool_invocations"] = {
            "claude_code": VERSION.findall(observations.read_text(errors="replace"))
        }
    manifest["outputs"] = snapshot_tree(root / "project", root / ".ccbench" / "objects", env)
    write_manifest(root, manifest)


def observed_models(root: Path) -> list[str]:
    """Read model identifiers only, never retain session messages or tool output."""
    output = root / "output.json"
    models: set[str] = set()
    if not output.is_file():
        return []
    with output.open(errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            usage = event.get("modelUsage")
            if isinstance(usage, dict):
                models.update(str(name) for name in usage)
    return sorted(models)


def archive_before_retry(root: Path) -> None:
    """Keep original evidence but make mutated workspaces ineligible for reuse."""
    manifest = load_manifest(root)
    if not manifest:
        return
    archive = root / ".ccbench" / "attempts" / str(uuid4())
    archive.mkdir(parents=True)
    shutil.copy2(root / MANIFEST, archive / "manifest.json")
    for pattern in ("*.log", "*.json"):
        for artifact in root.glob(pattern):
            if artifact.is_file() and not artifact.is_symlink():
                shutil.copy2(artifact, archive / artifact.name)
    manifest["state"] = "retried"
    manifest.setdefault("retry_history", []).append(
        {"at": now(), "archive": str(archive.relative_to(root))}
    )
    write_manifest(root, manifest)
