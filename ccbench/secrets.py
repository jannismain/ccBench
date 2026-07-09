import getpass
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from . import paths
from .log import log
from .shards import parse_shard_entry

ENV_SAMPLE = ".env.sample"
ENV_FILE = ".env"
ENV_KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class SecretRequirement:
    shard_name: str
    key: str


def preflight_config_secrets(
    experiment_config: dict,
    selected_variants: list[tuple[str, list]],
    env: dict[str, str],
) -> dict[str, str]:
    """Resolve required config-shard secrets before task assembly starts."""
    requirements = collect_config_secret_requirements(experiment_config, selected_variants)
    if not requirements:
        return {}

    resolved: dict[str, str] = {}
    missing: list[SecretRequirement] = []

    for requirement in requirements:
        value = resolve_secret_value(requirement, env, resolved)
        if value is None:
            if not requirement_satisfied_by_overrides(
                requirement, experiment_config, selected_variants
            ):
                missing.append(requirement)
        else:
            resolved[requirement.key] = value

    if missing:
        if not sys.stdin.isatty():
            missing_labels = ", ".join(
                f"{item.shard_name}:{item.key}" for item in dedupe_requirements(missing)
            )
            sys.exit(f"Missing required config shard secrets: {missing_labels}")
        prompt_for_missing_secrets(missing, resolved)

    return resolved


def collect_config_secret_requirements(
    experiment_config: dict, selected_variants: list[tuple[str, list]]
) -> list[SecretRequirement]:
    requirements: list[SecretRequirement] = []
    seen: set[tuple[str, str]] = set()
    for entry in selected_config_entries(experiment_config, selected_variants):
        shard_name, _shard_env = parse_shard_entry(entry)
        sample_file = paths.FORGE / shard_name / ENV_SAMPLE
        for key in parse_required_secret_keys(sample_file):
            marker = (shard_name, key)
            if marker in seen:
                continue
            seen.add(marker)
            requirements.append(SecretRequirement(shard_name, key))
    return requirements


def selected_config_entries(
    experiment_config: dict, selected_variants: list[tuple[str, list]]
) -> list:
    entries: list = []
    seen: set[str] = set()
    base_configs = experiment_config.get("configs", [])
    for _variant_name, variant_configs in selected_variants:
        for entry in [*base_configs, *variant_configs]:
            shard_name, _shard_env = parse_shard_entry(entry)
            if shard_name in seen:
                continue
            seen.add(shard_name)
            entries.append(entry)
    return entries


def parse_required_secret_keys(sample_file: Path) -> list[str]:
    if not sample_file.exists():
        return []

    assigned_keys: set[str] = set()
    required_keys: list[str] = []
    referenced_keys: list[str] = []

    for raw_line in sample_file.read_text().splitlines():
        parsed = parse_env_assignment(raw_line)
        if not parsed:
            continue
        key, value = parsed
        assigned_keys.add(key)
        if is_placeholder_value(value):
            required_keys.append(key)
        referenced_keys.extend(extract_env_references(value))

    for key in referenced_keys:
        if key not in assigned_keys:
            required_keys.append(key)

    return dedupe_keys(required_keys)


def parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = ENV_KEY_RE.match(stripped)
    if not match:
        return None
    key, value = match.groups()
    return key, strip_inline_comment(value.strip())


def strip_inline_comment(value: str) -> str:
    if "#" not in value:
        return value
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].strip()
    return value


def is_placeholder_value(value: str) -> bool:
    unquoted = unquote_env_value(value)
    return (
        not unquoted
        or "..." in unquoted
        or unquoted.startswith("<")
        or unquoted.endswith("_HERE")
        or unquoted.endswith("_here")
    )


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def extract_env_references(value: str) -> list[str]:
    return [match.group(1) or match.group(2) for match in ENV_REF_RE.finditer(value)]


def resolve_secret_value(
    requirement: SecretRequirement,
    env: dict[str, str],
    resolved: dict[str, str],
) -> str | None:
    key = requirement.key
    if key in env:
        return env[key]
    if key in resolved:
        return resolved[key]

    for secret_file in secret_files_for_shard(requirement.shard_name):
        values = read_env_file(secret_file)
        if key in values:
            return values[key]
    return None


def requirement_satisfied_by_overrides(
    requirement: SecretRequirement,
    experiment_config: dict,
    selected_variants: list[tuple[str, list]],
) -> bool:
    for _variant_name, variant_configs in selected_variants:
        entries = [*(experiment_config.get("configs", []) or []), *variant_configs]
        if not any(parse_shard_entry(entry)[0] == requirement.shard_name for entry in entries):
            continue
        overrides = {
            key: str(value) for key, value in (experiment_config.get("env") or {}).items()
        }
        for entry in entries:
            _shard_name, shard_env = parse_shard_entry(entry)
            overrides.update(shard_env)
        if requirement.key not in overrides:
            return False
    return True


def secret_files_for_shard(shard_name: str) -> list[Path]:
    return [
        paths.CCBENCH_HOME / "secrets" / f"{shard_name}.env",
        paths.FORGE / shard_name / ENV_FILE,
    ]


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = dotenv_values(path, interpolate=False)
    return {key: value for key, value in values.items() if value is not None}


def prompt_for_missing_secrets(
    missing: list[SecretRequirement], resolved: dict[str, str]
) -> None:
    by_key = dedupe_requirements(missing)
    print("Config shards require missing secrets:")
    for requirement in by_key:
        value = getpass.getpass(f"{requirement.shard_name}:{requirement.key}: ")
        resolved[requirement.key] = value
        if confirm_save_secret(requirement):
            save_secret(requirement.shard_name, requirement.key, value)


def confirm_save_secret(requirement: SecretRequirement) -> bool:
    answer = input(
        f"Save {requirement.key} for {requirement.shard_name} "
        "in ~/.ccbench/secrets? [Y/n] "
    )
    return answer.strip().lower() not in {"n", "no"}


def save_secret(shard_name: str, key: str, value: str) -> None:
    secret_file = paths.CCBENCH_HOME / "secrets" / f"{shard_name}.env"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    values = read_env_file(secret_file)
    values[key] = value
    with secret_file.open("w") as f:
        for item_key in sorted(values):
            f.write(f"export {item_key}={shlex.quote(values[item_key])}\n")
    secret_file.chmod(0o600)
    log.info(f"Saved secret {key} for config shard {shard_name}")


def apply_resolved_secrets(
    env: dict[str, str], task_root: Path, secrets: dict[str, str]
) -> dict[str, str]:
    if not secrets:
        return env
    new_env = env.copy()
    for key, value in secrets.items():
        new_env.setdefault(key, value)

    env_file = task_root / ENV_FILE
    with env_file.open("a") as f:
        f.write("\n# === From: ccbench secrets ===\n")
        for key, value in secrets.items():
            f.write(f"{key}={shlex.quote(value)}\n")
    return new_env


def dedupe_keys(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def dedupe_requirements(requirements: list[SecretRequirement]) -> list[SecretRequirement]:
    seen: set[str] = set()
    result: list[SecretRequirement] = []
    for requirement in requirements:
        if requirement.key in seen:
            continue
        seen.add(requirement.key)
        result.append(requirement)
    return result
