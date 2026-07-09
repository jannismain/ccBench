"""Tests for ccBench file merging functionality."""

import importlib.util
import json
import os
import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w
import yaml
from typer.testing import CliRunner

from ccbench.cli import _preprocess_tokens, app
from ccbench.compare import (
    build_task_results,
    extract_from_claude_metrics,
    extract_from_cloc,
    extract_from_exact_answer,
    extract_from_judge_scores,
    extract_from_static_analysis,
    extract_from_test_pass_rate,
    extract_metrics_summary,
    reference_indices_for_task_entries,
    render_comparison_json,
    render_comparison_table,
    render_grouped_comparison_json,
    render_grouped_comparison_table,
    resolve_task_dirs,
)
from ccbench.experiment import (
    all_available_evals,
    build_ad_hoc_experiment_config,
    build_ad_hoc_experiment_name,
    run_experiment,
)
from ccbench.files import (
    copy_item,
    copy_shard_with_script_rename,
    copy_task_shard_first,
    deep_merge_dict,
)
from ccbench.paths import (
    CCBENCH_DIR,
    CCBENCH_HOME,
    CCBENCH_IGNORE,
    RESULTS,
    STAGING_SCRIPT,
)
from ccbench.retry import retry, select_retry_scripts
from ccbench.scripts import (
    ensure_project_git_repo,
    load_script_statuses,
    run_script_with_env_capture,
    run_scripts_with_env_propagation,
)
from ccbench.secrets import parse_required_secret_keys, preflight_config_secrets
from ccbench.shards import (
    apply_shard_env,
    parse_shard_entry,
    process_shard,
)


def load_exact_answer_module():
    module_path = Path(__file__).parent.parent / "evals" / "exact_answer" / "parse_answer.py"
    spec = importlib.util.spec_from_file_location("exact_answer_parse", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestPaths:
    """Tests for shared filesystem paths."""

    def test_default_results_dir_is_ccbench_home(self):
        """Default results are written outside the repository."""
        assert CCBENCH_HOME == Path.home() / ".ccbench"
        assert RESULTS == CCBENCH_HOME / "results"


class TestDeepMergeDict:
    """Tests for deep_merge_dict function."""

    def test_merge_flat_dicts(self):
        """Test merging flat dictionaries."""
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = deep_merge_dict(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_merge_nested_dicts(self):
        """Test merging nested dictionaries."""
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        overlay = {"a": {"y": 5, "z": 6}, "c": 7}
        result = deep_merge_dict(base, overlay)
        assert result == {"a": {"x": 1, "y": 5, "z": 6}, "b": 3, "c": 7}

    def test_merge_lists(self):
        """Test that lists are extended, not replaced."""
        base = {"items": [1, 2, 3]}
        overlay = {"items": [4, 5]}
        result = deep_merge_dict(base, overlay)
        assert result == {"items": [1, 2, 3, 4, 5]}

    def test_merge_deeply_nested(self):
        """Test deeply nested dictionary merging."""
        base = {"a": {"b": {"c": {"d": 1}}}}
        overlay = {"a": {"b": {"c": {"e": 2}}}}
        result = deep_merge_dict(base, overlay)
        assert result == {"a": {"b": {"c": {"d": 1, "e": 2}}}}

    def test_merge_overwrites_non_dict_values(self):
        """Test that non-dict values are overwritten."""
        base = {"a": "string", "b": 123}
        overlay = {"a": "new_string", "b": 456}
        result = deep_merge_dict(base, overlay)
        assert result == {"a": "new_string", "b": 456}

    def test_merge_empty_dicts(self):
        """Test merging with empty dictionaries."""
        base = {"a": 1}
        overlay = {}
        assert deep_merge_dict(base, overlay) == {"a": 1}
        assert deep_merge_dict({}, overlay) == {}

    def test_original_dicts_unchanged(self):
        """Test that original dictionaries are not modified."""
        base = {"a": 1}
        overlay = {"b": 2}
        result = deep_merge_dict(base, overlay)
        assert base == {"a": 1}
        assert overlay == {"b": 2}
        assert result == {"a": 1, "b": 2}


class TestCopyItem:
    """Tests for copy_item function."""

    @pytest.fixture
    def temp_source(self, tmp_path):
        """Create a temporary source directory."""
        source = tmp_path / "source"
        source.mkdir()
        return source

    @pytest.fixture
    def temp_target(self, tmp_path):
        """Create a temporary target directory."""
        target = tmp_path / "target"
        target.mkdir()
        return target

    def test_copy_simple_file(self, temp_source, temp_target):
        """Test copying a simple text file."""
        source_file = temp_source / "test.txt"
        source_file.write_text("content")

        copy_item(source_file, temp_target)

        target_file = temp_target / "test.txt"
        assert target_file.exists()
        assert target_file.read_text() == "content"

    def test_copy_json_no_conflict(self, temp_source, temp_target):
        """Test copying JSON file when target doesn't exist."""
        source_file = temp_source / "config.json"
        data = {"key": "value"}
        source_file.write_text(json.dumps(data))

        copy_item(source_file, temp_target)

        target_file = temp_target / "config.json"
        assert target_file.exists()
        assert json.loads(target_file.read_text()) == data

    def test_merge_json_files(self, temp_source, temp_target):
        """Test merging JSON files when both exist."""
        # Create target file
        target_file = temp_target / "config.json"
        target_data = {"a": 1, "b": {"x": 10}}
        target_file.write_text(json.dumps(target_data))

        # Create source file
        source_file = temp_source / "config.json"
        source_data = {"b": {"y": 20}, "c": 3}
        source_file.write_text(json.dumps(source_data))

        copy_item(source_file, temp_target)

        # Verify merge
        result = json.loads(target_file.read_text())
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_copy_toml_no_conflict(self, temp_source, temp_target):
        """Test copying TOML file when target doesn't exist."""
        source_file = temp_source / "config.toml"
        data = {"key": "value"}
        with source_file.open("wb") as f:
            tomli_w.dump(data, f)

        copy_item(source_file, temp_target)

        target_file = temp_target / "config.toml"
        assert target_file.exists()
        with target_file.open("rb") as f:
            assert tomllib.load(f) == data

    def test_merge_toml_files(self, temp_source, temp_target):
        """Test merging TOML files when both exist."""
        # Create target file
        target_file = temp_target / "config.toml"
        target_data = {"a": 1, "b": {"x": 10}}
        with target_file.open("wb") as f:
            tomli_w.dump(target_data, f)

        # Create source file
        source_file = temp_source / "config.toml"
        source_data = {"b": {"y": 20}, "c": 3}
        with source_file.open("wb") as f:
            tomli_w.dump(source_data, f)

        copy_item(source_file, temp_target)

        # Verify merge
        with target_file.open("rb") as f:
            result = tomllib.load(f)
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_copy_directory_empty(self, temp_source, temp_target):
        """Test copying an empty directory."""
        source_dir = temp_source / "empty_dir"
        source_dir.mkdir()

        copy_item(source_dir, temp_target)

        target_dir = temp_target / "empty_dir"
        assert target_dir.exists()
        assert target_dir.is_dir()
        assert list(target_dir.iterdir()) == []

    def test_copy_directory_with_files(self, temp_source, temp_target):
        """Test copying a directory with files."""
        source_dir = temp_source / "mydir"
        source_dir.mkdir()
        (source_dir / "file1.txt").write_text("content1")
        (source_dir / "file2.txt").write_text("content2")

        copy_item(source_dir, temp_target)

        target_dir = temp_target / "mydir"
        assert target_dir.exists()
        assert (target_dir / "file1.txt").read_text() == "content1"
        assert (target_dir / "file2.txt").read_text() == "content2"

    def test_copy_directory_with_json_merge(self, temp_source, temp_target):
        """Test copying directory with JSON files that should be merged."""
        # Create target directory with JSON file
        target_dir = temp_target / "config_dir"
        target_dir.mkdir()
        target_json = target_dir / "settings.json"
        target_json.write_text(json.dumps({"existing": "value"}))

        # Create source directory with JSON file
        source_dir = temp_source / "config_dir"
        source_dir.mkdir()
        source_json = source_dir / "settings.json"
        source_json.write_text(json.dumps({"new": "value"}))

        copy_item(source_dir, temp_target)

        # Verify merge
        result = json.loads(target_json.read_text())
        assert result == {"existing": "value", "new": "value"}

    def test_merge_claude_settings_in_directory(self, temp_source, temp_target):
        """Test merging .claude/settings.json specifically (real-world scenario)."""
        # Create base claude_code config with base settings
        base_claude_dir = temp_target / ".claude"
        base_claude_dir.mkdir()
        base_settings = base_claude_dir / "settings.json"
        base_settings.write_text(
            json.dumps(
                {
                    "model": "sonnet",
                    "hooks": {"pre-commit": ["lint"]},
                    "mcpServers": {"filesystem": {"command": "fs-server"}},
                }
            )
        )

        # Create tdd_guard config overlay
        overlay_claude_dir = temp_source / ".claude"
        overlay_claude_dir.mkdir()
        overlay_settings = overlay_claude_dir / "settings.json"
        overlay_settings.write_text(
            json.dumps(
                {
                    "hooks": {"pre-commit": ["tdd-guard"]},
                    "mcpServers": {"tdd-guard": {"command": "tdd-guard-server"}},
                }
            )
        )

        # Copy and merge
        copy_item(overlay_claude_dir, temp_target)

        # Verify deep merge preserved both configs
        result = json.loads(base_settings.read_text())
        assert result == {
            "model": "sonnet",
            "hooks": {"pre-commit": ["lint", "tdd-guard"]},
            "mcpServers": {
                "filesystem": {"command": "fs-server"},
                "tdd-guard": {"command": "tdd-guard-server"},
            },
        }

    def test_copy_nested_directories(self, temp_source, temp_target):
        """Test copying nested directory structures."""
        source_dir = temp_source / "parent"
        source_dir.mkdir()
        child_dir = source_dir / "child"
        child_dir.mkdir()
        (child_dir / "file.txt").write_text("nested")

        copy_item(source_dir, temp_target)

        target_child = temp_target / "parent" / "child" / "file.txt"
        assert target_child.exists()
        assert target_child.read_text() == "nested"

    def test_merge_complex_json_structure(self, temp_source, temp_target):
        """Test merging complex JSON structures."""
        # Target: base config
        target_file = temp_target / "settings.json"
        target_data = {
            "hooks": {"pre-commit": ["lint"]},
            "model": "sonnet",
            "features": {"autocomplete": True},
        }
        target_file.write_text(json.dumps(target_data))

        # Source: overlay config
        source_file = temp_source / "settings.json"
        source_data = {
            "hooks": {"pre-commit": ["test"]},
            "features": {"autocomplete": False, "newFeature": True},
        }
        source_file.write_text(json.dumps(source_data))

        copy_item(source_file, temp_target)

        result = json.loads(target_file.read_text())
        assert result == {
            "hooks": {"pre-commit": ["lint", "test"]},
            "model": "sonnet",
            "features": {"autocomplete": False, "newFeature": True},
        }

    def test_overwrite_non_json_file(self, temp_source, temp_target):
        """Test that non-JSON files are overwritten, not merged."""
        target_file = temp_target / "file.txt"
        target_file.write_text("old content")

        source_file = temp_source / "file.txt"
        source_file.write_text("new content")

        copy_item(source_file, temp_target)

        assert target_file.read_text() == "new content"

    def test_invalid_json_fallback(self, temp_source, temp_target):
        """Test that invalid JSON falls back to overwrite."""
        # Create target with valid JSON
        target_file = temp_target / "config.json"
        target_file.write_text('{"key": "value"}')

        # Create source with invalid JSON
        source_file = temp_source / "config.json"
        source_file.write_text("not valid json{")

        copy_item(source_file, temp_target)

        # Should overwrite with invalid content
        assert target_file.read_text() == "not valid json{"

    def test_invalid_toml_fallback(self, temp_source, temp_target):
        """Test that invalid TOML falls back to overwrite."""
        # Create target with valid TOML
        target_file = temp_target / "config.toml"
        with target_file.open("wb") as f:
            tomli_w.dump({"key": "value"}, f)

        # Create source with invalid TOML
        source_file = temp_source / "config.toml"
        source_file.write_text("not valid toml [[[")

        copy_item(source_file, temp_target)

        # Should overwrite with invalid content
        assert target_file.read_text() == "not valid toml [[["

    def test_append_env_file_first_shard(self, temp_source, temp_target):
        """Test appending .env file when target doesn't exist (first shard)."""
        source_file = temp_source / ".env"
        source_file.write_text("VAR1=value1\nVAR2=value2\n")

        copy_item(source_file, temp_target, source_name="config_shard")

        target_file = temp_target / ".env"
        assert target_file.exists()
        content = target_file.read_text()
        assert "# === From: config_shard ===" in content
        assert "VAR1=value1" in content
        assert "VAR2=value2" in content

    def test_append_env_file_multiple_shards(self, temp_source, temp_target):
        """Test appending .env files from multiple shards."""
        # First shard
        source_file1 = temp_source / ".env"
        source_file1.write_text("VAR1=value1\n")
        copy_item(source_file1, temp_target, source_name="first_shard")

        # Second shard
        source_file2 = temp_source / ".env"
        source_file2.write_text("VAR2=value2\n")
        copy_item(source_file2, temp_target, source_name="second_shard")

        # Third shard
        source_file3 = temp_source / ".env"
        source_file3.write_text("VAR3=value3\n")
        copy_item(source_file3, temp_target, source_name="third_shard")

        target_file = temp_target / ".env"
        content = target_file.read_text()

        # Verify all sections are present with markers
        assert "# === From: first_shard ===" in content
        assert "VAR1=value1" in content
        assert "# === From: second_shard ===" in content
        assert "VAR2=value2" in content
        assert "# === From: third_shard ===" in content
        assert "VAR3=value3" in content

        # Verify order is preserved
        first_pos = content.index("first_shard")
        second_pos = content.index("second_shard")
        third_pos = content.index("third_shard")
        assert first_pos < second_pos < third_pos

    def test_append_env_sample_file(self, temp_source, temp_target):
        """Test that .env.sample files are also appended with markers."""
        source_file = temp_source / ".env.sample"
        source_file.write_text("SAMPLE_VAR=sample_value\n")

        copy_item(source_file, temp_target, source_name="config_shard")

        target_file = temp_target / ".env.sample"
        assert target_file.exists()
        content = target_file.read_text()
        assert "# === From: config_shard ===" in content
        assert "SAMPLE_VAR=sample_value" in content

    def test_env_file_no_source_name_falls_back(self, temp_source, temp_target):
        """Test that .env without source_name falls back to normal copy."""
        source_file = temp_source / ".env"
        source_file.write_text("VAR=value\n")

        # Call without source_name (e.g., from test code)
        copy_item(source_file, temp_target)

        target_file = temp_target / ".env"
        assert target_file.exists()
        # Should just copy without marker
        content = target_file.read_text()
        assert "VAR=value" in content

    def test_env_file_empty_skipped(self, temp_source, temp_target):
        """Test that empty .env files are skipped."""
        # Create target with existing content
        target_file = temp_target / ".env"
        target_file.write_text("# === From: first_shard ===\nVAR1=value1\n")

        # Create empty source file
        source_file = temp_source / ".env"
        source_file.write_text("")

        copy_item(source_file, temp_target, source_name="empty_shard")

        # Target should be unchanged
        content = target_file.read_text()
        assert "empty_shard" not in content
        assert "VAR1=value1" in content

    def test_env_file_without_trailing_newline(self, temp_source, temp_target):
        """Test appending .env file that doesn't end with newline."""
        source_file = temp_source / ".env"
        source_file.write_text("VAR=value")  # No trailing newline

        copy_item(source_file, temp_target, source_name="test_shard")

        target_file = temp_target / ".env"
        content = target_file.read_text()
        # Should end with newline after appending
        assert content.endswith("\n")
        assert "VAR=value" in content

    def test_env_file_proper_spacing(self, temp_source, temp_target):
        """Test that proper spacing is maintained between sections."""
        # First shard
        source_file1 = temp_source / ".env"
        source_file1.write_text("VAR1=value1\n")
        copy_item(source_file1, temp_target, source_name="first")

        # Second shard
        source_file2 = temp_source / ".env"
        source_file2.write_text("VAR2=value2\n")
        copy_item(source_file2, temp_target, source_name="second")

        target_file = temp_target / ".env"
        content = target_file.read_text()

        # There should be blank line between sections
        assert "\n\n# === From: second ===" in content

    def test_real_world_env_scenario(self, temp_source, temp_target):
        """Test real-world scenario: config -> task -> eval .env files."""
        # Config shard with Claude Code API key
        config_env = temp_source / ".env"
        config_env.write_text("ANTHROPIC_API_KEY=sk-ant-test123\nMODEL=sonnet\n")
        copy_item(config_env, temp_target, source_name="claude_code")

        # Task shard with task-specific vars
        task_env = temp_source / ".env"
        task_env.write_text("TASK_ID=aoc_2025_01\nDIFFICULTY=hard\n")
        copy_item(task_env, temp_target, source_name="aoc_2025_01")

        # Eval shard with eval settings
        eval_env = temp_source / ".env"
        eval_env.write_text("ENABLE_METRICS=true\nTIMEOUT=300\n")
        copy_item(eval_env, temp_target, source_name="metrics_eval")

        target_file = temp_target / ".env"
        content = target_file.read_text()

        # Verify all sections are present and properly marked
        assert "# === From: claude_code ===" in content
        assert "ANTHROPIC_API_KEY=sk-ant-test123" in content
        assert "# === From: aoc_2025_01 ===" in content
        assert "TASK_ID=aoc_2025_01" in content
        assert "# === From: metrics_eval ===" in content
        assert "ENABLE_METRICS=true" in content

        # Verify order
        lines = content.split("\n")
        marker_lines = [i for i, line in enumerate(lines) if "# === From:" in line]
        assert len(marker_lines) == 3  # Three sections


class TestScriptExecution:
    """Tests for script execution with renaming and environment propagation."""

    @pytest.fixture
    def temp_shard(self, tmp_path):
        """Create a temporary shard directory."""
        shard = tmp_path / "shard"
        shard.mkdir()
        return shard

    @pytest.fixture
    def temp_project(self, tmp_path):
        """Create a temporary project directory (agent workspace)."""
        project = tmp_path / "project"
        project.mkdir()
        return project

    @pytest.fixture
    def temp_task_root(self, tmp_path):
        """Create a temporary task root directory (ccBench infrastructure)."""
        task_root = tmp_path / "task_root"
        task_root.mkdir()
        return task_root

    def test_scripts_routed_to_task_root(self, temp_shard, temp_project, temp_task_root):
        """Scripts at shard root are renamed and routed to task_root_dir."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho hello")
        (temp_shard / "setup.sh").write_text("#!/bin/bash\necho setup")
        (temp_shard / "other.txt").write_text("not a script")

        copy_shard_with_script_rename(temp_shard, temp_project, temp_task_root, 0, "my_shard")

        assert (temp_task_root / "run.000.my_shard.sh").exists()
        assert (temp_task_root / "setup.000.my_shard.sh").exists()
        assert not (temp_task_root / "run.sh").exists()
        assert not (temp_task_root / "setup.sh").exists()
        # Non-script files at shard root go to task_root
        assert (temp_task_root / "other.txt").exists()
        # Nothing in project_dir
        assert not (temp_project / "run.000.my_shard.sh").exists()
        assert not (temp_project / "other.txt").exists()

    def test_project_subdir_routes_to_project_dir(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Files inside shard's project/ subdir are routed to project_dir."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho run")
        project_sub = temp_shard / "project"
        project_sub.mkdir()
        claude_dir = project_sub / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"model": "sonnet"}))
        (project_sub / "CLAUDE.md").write_text("# Agent instructions")

        copy_shard_with_script_rename(temp_shard, temp_project, temp_task_root, 0, "my_shard")

        # Scripts go to task_root
        assert (temp_task_root / "run.000.my_shard.sh").exists()
        # Project files go to project_dir
        assert (temp_project / ".claude" / "settings.json").exists()
        assert json.loads((temp_project / ".claude" / "settings.json").read_text()) == {
            "model": "sonnet"
        }
        assert (temp_project / "CLAUDE.md").exists()
        # Project files NOT in task_root
        assert not (temp_task_root / ".claude").exists()
        assert not (temp_task_root / "CLAUDE.md").exists()

    def test_project_subdir_json_merge(self, temp_shard, temp_project, temp_task_root):
        """JSON files from project/ subdirs of multiple shards are deep-merged."""
        # First shard
        shard1 = temp_shard
        proj1 = shard1 / "project"
        proj1.mkdir()
        claude1 = proj1 / ".claude"
        claude1.mkdir()
        (claude1 / "settings.json").write_text(
            json.dumps({"env": {"KEY1": "val1"}, "model": "sonnet"})
        )
        copy_shard_with_script_rename(shard1, temp_project, temp_task_root, 0, "shard1")

        # Second shard
        shard2 = temp_task_root.parent / "shard2"
        shard2.mkdir()
        proj2 = shard2 / "project"
        proj2.mkdir()
        claude2 = proj2 / ".claude"
        claude2.mkdir()
        (claude2 / "settings.json").write_text(json.dumps({"env": {"KEY2": "val2"}}))
        copy_shard_with_script_rename(shard2, temp_project, temp_task_root, 1, "shard2")

        result = json.loads((temp_project / ".claude" / "settings.json").read_text())
        assert result == {"env": {"KEY1": "val1", "KEY2": "val2"}, "model": "sonnet"}

    def test_shard_without_project_subdir(self, temp_shard, temp_project, temp_task_root):
        """Shard without project/ subdir routes everything to task_root."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho run")
        (temp_shard / "cc_metrics.py").write_text("print('metrics')")

        copy_shard_with_script_rename(
            temp_shard, temp_project, temp_task_root, 0, "eval_shard"
        )

        assert (temp_task_root / "run.000.eval_shard.sh").exists()
        assert (temp_task_root / "cc_metrics.py").exists()
        # project_dir should be empty
        assert list(temp_project.iterdir()) == []

    def test_project_subdir_env_file(self, temp_shard, temp_project, temp_task_root):
        """Env file in project/ subdir routes to project_dir."""
        proj = temp_shard / "project"
        proj.mkdir()
        (proj / ".env").write_text("AGENT_VAR=value\n")

        copy_shard_with_script_rename(temp_shard, temp_project, temp_task_root, 0, "my_shard")

        assert (temp_project / ".env").exists()
        assert "AGENT_VAR=value" in (temp_project / ".env").read_text()
        assert not (temp_task_root / ".env").exists()

    def test_scripts_renamed_with_padding(self, temp_shard, temp_project, temp_task_root):
        """Scripts use zero-padded indices for correct alphabetical sorting."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho test")

        copy_shard_with_script_rename(temp_shard, temp_project, temp_task_root, 5, "shard")
        assert (temp_task_root / "run.005.shard.sh").exists()

        copy_shard_with_script_rename(temp_shard, temp_project, temp_task_root, 42, "another")
        assert (temp_task_root / "run.042.another.sh").exists()

    def test_copy_without_merge_overwrites_existing_json(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Fast task copies overwrite JSON instead of merging it."""
        existing_dir = temp_project / ".claude"
        existing_dir.mkdir()
        existing_settings = existing_dir / "settings.json"
        existing_settings.write_text(json.dumps({"config": True, "shared": "old"}))

        project_sub = temp_shard / "project"
        project_sub.mkdir()
        task_claude_dir = project_sub / ".claude"
        task_claude_dir.mkdir()
        (task_claude_dir / "settings.json").write_text(
            json.dumps({"task": True, "shared": "new"})
        )

        copy_shard_with_script_rename(
            temp_shard,
            temp_project,
            temp_task_root,
            1,
            "task_shard",
            merge_files=False,
        )

        assert json.loads(existing_settings.read_text()) == {"task": True, "shared": "new"}

    def test_copy_task_shard_first_allows_later_config_merge(
        self, temp_project, temp_task_root
    ):
        """Task files land first without merging, then config overlays merge onto them."""
        task_shard = temp_task_root.parent / "task_shard"
        task_shard.mkdir()
        task_project = task_shard / "project"
        task_project.mkdir()
        task_claude_dir = task_project / ".claude"
        task_claude_dir.mkdir()
        (task_claude_dir / "settings.json").write_text(
            json.dumps({"hooks": {"pre-commit": ["task"]}, "task": True})
        )
        (task_shard / "run.sh").write_text("#!/bin/bash\necho task")

        copied = copy_task_shard_first(task_shard, temp_project, temp_task_root, 1, "task")

        assert copied
        assert (temp_task_root / "run.001.task.sh").exists()

        config_shard = temp_task_root.parent / "config_shard"
        config_shard.mkdir()
        config_project = config_shard / "project"
        config_project.mkdir()
        config_claude_dir = config_project / ".claude"
        config_claude_dir.mkdir()
        (config_claude_dir / "settings.json").write_text(
            json.dumps({"hooks": {"pre-commit": ["config"]}, "model": "sonnet"})
        )
        (config_shard / "run.sh").write_text("#!/bin/bash\necho config")

        copy_shard_with_script_rename(config_shard, temp_project, temp_task_root, 0, "config")

        assert json.loads((temp_project / ".claude" / "settings.json").read_text()) == {
            "hooks": {"pre-commit": ["task", "config"]},
            "task": True,
            "model": "sonnet",
        }
        assert (temp_task_root / "run.000.config.sh").exists()

    def test_copy_task_shard_first_falls_back_when_staging_exists(
        self, temp_project, temp_task_root
    ):
        """Tasks with staging stay on the existing shard-processing path."""
        task_shard = temp_task_root.parent / "task_with_staging"
        task_shard.mkdir()
        (task_shard / STAGING_SCRIPT).write_text("#!/bin/bash\necho staging")
        (task_shard / "run.sh").write_text("#!/bin/bash\necho task")

        copied = copy_task_shard_first(
            task_shard, temp_project, temp_task_root, 1, "task_with_staging"
        )

        assert not copied
        assert not (temp_task_root / "run.001.task_with_staging.sh").exists()

    def test_env_propagates_between_scripts(self, tmp_path):
        """Environment variables propagate to subsequent scripts."""
        (tmp_path / "setup.000.first.sh").write_text("#!/bin/bash\nexport FOO=bar")
        (tmp_path / "setup.001.second.sh").write_text('#!/bin/bash\necho "FOO=$FOO"')

        env = os.environ.copy()
        success, final_env = run_scripts_with_env_propagation(
            "setup.*.sh", tmp_path, env, stop_on_failure=True
        )

        assert success
        assert final_env.get("FOO") == "bar"

    def test_env_capture_works_with_relative_working_dir(self, tmp_path, monkeypatch):
        """Env capture writes inside cwd even when working_dir is relative."""
        work_dir = tmp_path / "results" / "demo" / "tasks" / "task"
        work_dir.mkdir(parents=True)
        script = work_dir / "setup.000.relative.sh"
        script.write_text("#!/bin/bash\nexport RELATIVE_CAPTURE=ok\n")

        monkeypatch.chdir(tmp_path)
        relative_work_dir = Path("results") / "demo" / "tasks" / "task"

        return_code, final_env = run_script_with_env_capture(
            relative_work_dir / script.name,
            relative_work_dir,
            os.environ.copy(),
        )

        assert return_code == 0
        assert final_env["RELATIVE_CAPTURE"] == "ok"
        assert not (work_dir / "results").exists()

    def test_setup_stops_on_failure(self, tmp_path):
        """Setup scripts stop execution on failure."""
        (tmp_path / "setup.000.first.sh").write_text("#!/bin/bash\nexit 1")
        (tmp_path / "setup.001.second.sh").write_text("#!/bin/bash\necho should not run")

        env = os.environ.copy()
        success, _ = run_scripts_with_env_propagation(
            "setup.*.sh", tmp_path, env, stop_on_failure=True
        )

        assert not success
        # Second script's log should not exist
        assert not (tmp_path / "setup.001.second.log").exists()
        statuses = load_script_statuses(tmp_path)["scripts"]
        assert statuses["setup.000.first.sh"]["return_code"] == 1

    def test_run_continues_on_failure(self, tmp_path):
        """Run scripts continue execution on failure."""
        (tmp_path / "run.000.first.sh").write_text("#!/bin/bash\nexit 1")
        (tmp_path / "run.001.second.sh").write_text("#!/bin/bash\necho continued")

        env = os.environ.copy()
        success, _ = run_scripts_with_env_propagation(
            "run.*.sh", tmp_path, env, stop_on_failure=False
        )

        # Returns False because there was a failure
        assert not success
        # But second script's log should exist (it ran)
        assert (tmp_path / "run.001.second.log").exists()
        statuses = load_script_statuses(tmp_path)["scripts"]
        assert statuses["run.000.first.sh"]["return_code"] == 1
        assert statuses["run.001.second.sh"]["return_code"] == 0

    def test_shard_ordering(self, tmp_path):
        """Scripts execute in config -> task -> eval order via consecutive indices."""
        # Create scripts simulating: 2 config, 1 task, 1 eval
        (tmp_path / "run.000.claude_code.sh").write_text("#!/bin/bash\necho config0")
        (tmp_path / "run.001.tdd_guard.sh").write_text("#!/bin/bash\necho config1")
        (tmp_path / "run.002.aoc_2025_01.sh").write_text("#!/bin/bash\necho task")
        (tmp_path / "run.003.cloc.sh").write_text("#!/bin/bash\necho eval")

        scripts = sorted(tmp_path.glob("run.*.sh"))
        names = [s.name for s in scripts]

        assert names == [
            "run.000.claude_code.sh",
            "run.001.tdd_guard.sh",
            "run.002.aoc_2025_01.sh",
            "run.003.cloc.sh",
        ]

    def test_output_captured_to_log(self, tmp_path):
        """Script output is captured to log file."""
        (tmp_path / "run.000.test.sh").write_text(
            "#!/bin/bash\necho 'hello stdout'\necho 'hello stderr' >&2"
        )

        env = os.environ.copy()
        run_scripts_with_env_propagation("run.*.sh", tmp_path, env, stop_on_failure=False)

        log_file = tmp_path / "run.000.test.log"
        assert log_file.exists()
        log_content = log_file.read_text()
        assert "hello stdout" in log_content
        assert "hello stderr" in log_content

    def test_cloc_eval_uses_git_diff_changed_files(self, tmp_path):
        """cloc eval counts changed tracked files and untracked files from project git state."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "existing.py").write_text("print('base')\n")
        (project_dir / "untouched.py").write_text("print('untouched')\n")
        ensure_project_git_repo(project_dir)

        (project_dir / "existing.py").write_text("print('base')\nprint('changed')\n")
        (project_dir / "new.js").write_text("console.log('new');\n")

        script_source = CCBENCH_DIR / "evals" / "cloc" / "run.sh"
        (tmp_path / "run.000.cloc.sh").write_text(script_source.read_text())

        env = os.environ.copy()
        success, _ = run_scripts_with_env_propagation(
            "run.*.sh", tmp_path, env, stop_on_failure=False
        )

        assert success
        cloc_output = json.loads((tmp_path / "cloc.json").read_text())
        assert cloc_output["header"]["n_files"] == 2
        assert cloc_output["SUM"]["nFiles"] == 2
        assert cloc_output["Python"]["nFiles"] == 1
        assert cloc_output["JavaScript"]["nFiles"] == 1

    def test_process_shard_without_staging_script(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Process shard without staging script copies directly with routing."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho run")
        proj = temp_shard / "project"
        proj.mkdir()
        (proj / "file.txt").write_text("content")

        env = os.environ.copy()
        returned_env = process_shard(
            temp_shard, temp_project, temp_task_root, 0, "test_shard", env
        )

        assert (temp_task_root / "run.000.test_shard.sh").exists()
        assert (temp_project / "file.txt").exists()
        assert not (temp_task_root / "file.txt").exists()
        assert returned_env is not None

    def test_ccbenchignore_excludes_files(self, temp_shard, temp_project, temp_task_root):
        """Files matching .ccbenchignore patterns are excluded from copy."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho run")
        (temp_shard / "file.txt").write_text("keep me")
        (temp_shard / ".dev").mkdir()
        (temp_shard / ".dev" / "notes.md").write_text("dev notes")
        (temp_shard / "secret.key").write_text("secret")
        (temp_shard / "__pycache__").mkdir()
        (temp_shard / "__pycache__" / "ignored.pyc").write_text("cache")
        (temp_shard / CCBENCH_IGNORE).write_text(".dev\n*.key\n")

        copy_shard_with_script_rename(
            temp_shard, temp_project, temp_task_root, 0, "test_shard"
        )

        assert (temp_task_root / "run.000.test_shard.sh").exists()
        assert (temp_task_root / "file.txt").exists()
        assert not (temp_task_root / ".dev").exists()
        assert not (temp_task_root / "secret.key").exists()
        assert not (temp_task_root / "__pycache__").exists()
        assert not (temp_task_root / CCBENCH_IGNORE).exists()

    def test_no_ccbenchignore_copies_everything(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Without .ccbenchignore, only the built-in ignored paths are skipped."""
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho run")
        (temp_shard / ".dev").mkdir()
        (temp_shard / ".dev" / "notes.md").write_text("dev notes")
        (temp_shard / "__pycache__").mkdir()
        (temp_shard / "__pycache__" / "notes.pyc").write_text("cache")
        (temp_shard / ".npm").mkdir()
        (temp_shard / ".npm" / "_logs").mkdir()
        (temp_shard / ".npm" / "_logs" / "debug.log").write_text("log")

        copy_shard_with_script_rename(
            temp_shard, temp_project, temp_task_root, 0, "test_shard"
        )

        assert (temp_task_root / "run.000.test_shard.sh").exists()
        assert (temp_task_root / ".dev" / "notes.md").exists()
        assert not (temp_task_root / "__pycache__").exists()
        assert not (temp_task_root / ".npm").exists()

    def test_default_ignore_patterns_apply_recursively(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Built-in ignores also exclude nested __pycache__ and .npm directories."""
        project_dir = temp_shard / "project"
        project_dir.mkdir()
        package_dir = project_dir / "src"
        package_dir.mkdir()
        (package_dir / "main.py").write_text("print('ok')")
        (package_dir / "__pycache__").mkdir()
        (package_dir / "__pycache__" / "main.cpython-314.pyc").write_text("cache")
        (project_dir / ".npm").mkdir()
        (project_dir / ".npm" / "cache").mkdir()
        (project_dir / ".npm" / "cache" / "index.json").write_text("{}")

        copy_shard_with_script_rename(
            temp_shard, temp_project, temp_task_root, 0, "test_shard"
        )

        assert (temp_project / "src" / "main.py").exists()
        assert not (temp_project / "src" / "__pycache__").exists()
        assert not (temp_project / ".npm").exists()

    def test_process_shard_with_staging_script(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Process shard with staging script runs it in staging before copy."""
        # Create a staging script that modifies run.sh
        (temp_shard / STAGING_SCRIPT).write_text(
            "#!/bin/bash\necho '#!/bin/bash' > run.sh\necho 'echo modified' >> run.sh"
        )
        (temp_shard / "run.sh").write_text("#!/bin/bash\necho original")

        env = os.environ.copy()
        returned_env = process_shard(
            temp_shard, temp_project, temp_task_root, 0, "test_shard", env
        )

        # The copied run.sh should be the modified version at task_root
        modified_script = temp_task_root / "run.000.test_shard.sh"
        assert modified_script.exists()
        assert "modified" in modified_script.read_text()
        assert "original" not in modified_script.read_text()
        assert returned_env is not None

    def test_staging_script_env_propagates_to_setup(
        self, temp_shard, temp_project, temp_task_root
    ):
        """Environment variables from staging.sh propagate to setup.sh."""
        # Create a staging script that exports a variable
        (temp_shard / STAGING_SCRIPT).write_text(
            "#!/bin/bash\nexport STAGING_VAR=from_staging"
        )
        (temp_shard / "setup.sh").write_text("#!/bin/bash\necho setup")

        env = os.environ.copy()
        env = process_shard(temp_shard, temp_project, temp_task_root, 0, "test_shard", env)

        # The env returned should contain the variable from staging.sh
        assert env.get("STAGING_VAR") == "from_staging"

        # Now run setup scripts - they should see the variable
        (temp_task_root / "setup.000.test_shard.sh").write_text(
            '#!/bin/bash\necho "STAGING_VAR=$STAGING_VAR"'
        )
        success, final_env = run_scripts_with_env_propagation(
            "setup.*.sh", temp_task_root, env, stop_on_failure=True
        )

        assert success
        assert final_env.get("STAGING_VAR") == "from_staging"

    def test_multiple_env_vars_propagate(self, tmp_path):
        """Multiple environment variables propagate correctly."""
        (tmp_path / "setup.000.first.sh").write_text(
            "#!/bin/bash\nexport VAR1=value1\nexport VAR2=value2"
        )
        (tmp_path / "setup.001.second.sh").write_text("#!/bin/bash\nexport VAR3=value3")

        env = os.environ.copy()
        success, final_env = run_scripts_with_env_propagation(
            "setup.*.sh", tmp_path, env, stop_on_failure=True
        )

        assert success
        assert final_env.get("VAR1") == "value1"
        assert final_env.get("VAR2") == "value2"
        assert final_env.get("VAR3") == "value3"

    def test_env_modification_persists(self, tmp_path):
        """Environment modifications in one script persist to next."""
        (tmp_path / "setup.000.first.sh").write_text(
            "#!/bin/bash\nexport PATH=/custom/path:$PATH"
        )
        (tmp_path / "setup.001.second.sh").write_text('#!/bin/bash\necho "PATH=$PATH"')

        env = os.environ.copy()
        original_path = env.get("PATH", "")

        success, final_env = run_scripts_with_env_propagation(
            "setup.*.sh", tmp_path, env, stop_on_failure=True
        )

        assert success
        # PATH should have been modified
        assert "/custom/path" in final_env.get("PATH", "")
        # And should still contain original PATH
        assert original_path in final_env.get("PATH", "")


class TestProjectGitRepo:
    """Tests for git repo placement in the agent workspace."""

    @staticmethod
    def run_git(args: list[str], cwd):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def test_ensure_project_git_repo_initializes_in_project_dir(self, tmp_path):
        task_root = tmp_path / "task_root"
        task_root.mkdir()
        project_dir = task_root / "project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("hello\n")

        ensure_project_git_repo(project_dir)

        assert (project_dir / ".git").exists()
        assert not (task_root / ".git").exists()
        assert self.run_git(["rev-parse", "--show-toplevel"], project_dir) == str(
            project_dir.resolve()
        )
        assert self.run_git(["log", "--format=%s", "-1"], project_dir) == (
            "ccbench initial state"
        )

    def test_ensure_project_git_repo_creates_nested_repo_inside_parent_checkout(
        self, tmp_path
    ):
        outer_repo = tmp_path / "outer_repo"
        outer_repo.mkdir()
        self.run_git(["init", "--quiet"], outer_repo)
        (outer_repo / "tracked.txt").write_text("outer\n")
        self.run_git(["add", "tracked.txt"], outer_repo)
        self.run_git(
            [
                "-c",
                "user.name=Outer Repo",
                "-c",
                "user.email=outer@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "Outer commit",
            ],
            outer_repo,
        )

        task_root = outer_repo / "ccBench" / "results" / "exp" / "tasks" / "demo"
        project_dir = task_root / "project"
        project_dir.mkdir(parents=True)
        (project_dir / "README.md").write_text("inner\n")

        assert self.run_git(["rev-parse", "--show-toplevel"], task_root) == str(
            outer_repo.resolve()
        )

        ensure_project_git_repo(project_dir)

        assert self.run_git(["rev-parse", "--show-toplevel"], task_root) == str(
            outer_repo.resolve()
        )
        assert self.run_git(["rev-parse", "--show-toplevel"], project_dir) == str(
            project_dir.resolve()
        )

    def test_ensure_project_git_repo_preserves_existing_project_repo(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("upstream\n")
        self.run_git(["init", "--quiet"], project_dir)
        self.run_git(["add", "README.md"], project_dir)
        self.run_git(
            [
                "-c",
                "user.name=Upstream Repo",
                "-c",
                "user.email=upstream@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "Upstream commit",
            ],
            project_dir,
        )
        head_before = self.run_git(["rev-parse", "HEAD"], project_dir)

        ensure_project_git_repo(project_dir)

        assert self.run_git(["rev-parse", "HEAD"], project_dir) == head_before
        assert self.run_git(["log", "--format=%s", "-1"], project_dir) == "Upstream commit"


class TestParseShardEntry:
    """Tests for parse_shard_entry function."""

    def test_string_entry(self):
        name, env = parse_shard_entry("claude_code")
        assert name == "claude_code"
        assert env == {}

    def test_dict_entry_with_env(self):
        entry = {"openspec": {"env": {"TOOLS": "claude,cursor", "MODE": "dev"}}}
        name, env = parse_shard_entry(entry)
        assert name == "openspec"
        assert env == {"TOOLS": "claude,cursor", "MODE": "dev"}

    def test_dict_entry_without_env(self):
        name, env = parse_shard_entry({"openspec": {}})
        assert name == "openspec"
        assert env == {}

    def test_dict_entry_none_value(self):
        name, env = parse_shard_entry({"openspec": None})
        assert name == "openspec"
        assert env == {}

    def test_dict_entry_numeric_value_converted_to_str(self):
        entry = {"shard": {"env": {"PORT": 8080}}}
        _, env = parse_shard_entry(entry)
        assert env["PORT"] == "8080"

    def test_dict_entry_multiple_keys_raises(self):
        with pytest.raises(ValueError, match="exactly one key"):
            parse_shard_entry({"a": {}, "b": {}})

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            parse_shard_entry(42)


class TestApplyShardEnv:
    """Tests for apply_shard_env function."""

    def test_empty_env_is_noop(self, tmp_path):
        env = {"EXISTING": "val"}
        result = apply_shard_env({}, env, tmp_path, "shard")
        assert result == {"EXISTING": "val"}
        assert not (tmp_path / ".env").exists()

    def test_applies_env_to_dict(self, tmp_path):
        env = {"EXISTING": "val"}
        result = apply_shard_env({"TOOLS": "claude"}, env, tmp_path, "shard")
        assert result["TOOLS"] == "claude"
        assert result["EXISTING"] == "val"

    def test_appends_to_env_file(self, tmp_path):
        (tmp_path / ".env").write_text("OLD=value\n")
        apply_shard_env({"TOOLS": "claude"}, {}, tmp_path, "openspec")
        content = (tmp_path / ".env").read_text()
        assert "# === From: openspec (experiment override) ===" in content
        assert "TOOLS=claude" in content
        assert "OLD=value" in content


class TestEnvPrecedence:
    """Shard-level YAML env must override experiment-level YAML env.

    Uses experiments/test_model_override.yaml which sets:
      - experiment env: ANTHROPIC_MODEL=haiku
      - sonnet variant shard env: ANTHROPIC_MODEL=sonnet
    The sonnet variant's shard-level override must win.
    """

    def _run_experiment(self, tmp_path):
        """Run test_model_override with --skip-run, results written to tmp_path."""
        result = subprocess.run(
            ["uv", "run", "ccbench", "test_model_override", "--skip-run"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "CCBENCH_RESULT": str(tmp_path),
                "REQUESTY_API_KEY": "test-secret",
            },
        )
        assert result.returncode == 0, f"Experiment failed: {result.stderr}"

        experiment_dirs = sorted(tmp_path.glob("*_test_model_override"))
        assert experiment_dirs, "No experiment output directory found"
        return experiment_dirs[-1]

    def test_shard_env_overrides_experiment_env(self, tmp_path):
        """Shard-level ANTHROPIC_MODEL=sonnet must beat experiment-level haiku."""
        experiment_dir = self._run_experiment(tmp_path)
        sonnet_dir = experiment_dir / "tasks" / "debug_sonnet"

        sonnet_env = (sonnet_dir / ".env").read_text()
        last_model_line = [
            line for line in sonnet_env.splitlines() if line.startswith("ANTHROPIC_MODEL=")
        ][-1]
        assert last_model_line == "ANTHROPIC_MODEL=sonnet", (
            f"Shard-level env should be last and win, got: {last_model_line}\n"
            f"Full .env:\n{sonnet_env}"
        )

    def test_experiment_env_applies_without_shard_override(self, tmp_path):
        """Baseline variant has no shard override — experiment-level env stands."""
        experiment_dir = self._run_experiment(tmp_path)
        baseline_dir = experiment_dir / "tasks" / "debug_baseline"

        baseline_env = (baseline_dir / ".env").read_text()
        assert "ANTHROPIC_MODEL=haiku" in baseline_env


class TestConfigSecrets:
    """Tests for config-shard secret discovery and preflight resolution."""

    def test_parse_required_secret_keys_from_placeholders_and_refs(self, tmp_path):
        """Placeholder values and unassigned variable refs declare required secrets."""
        sample_file = tmp_path / ".env.sample"
        sample_file.write_text(
            "\n".join(
                [
                    "export API_KEY=sk-...",
                    "ANTHROPIC_AUTH_TOKEN=${API_KEY}",
                    "DERIVED_TOKEN=${EXTERNAL_TOKEN}",
                    "BASE_URL=https://example.com",
                    "EMPTY_SECRET=",
                    "COMMENTED=value # not a secret",
                ]
            )
        )

        assert parse_required_secret_keys(sample_file) == [
            "API_KEY",
            "EMPTY_SECRET",
            "EXTERNAL_TOKEN",
        ]

    def test_preflight_prompts_for_missing_secret_and_saves_it(
        self, tmp_path, monkeypatch
    ):
        """Interactive first usage prompts once and writes the ccBench secret store."""
        forge_dir = tmp_path / "config_forge"
        home_dir = tmp_path / "home"
        shard_dir = forge_dir / "alpha"
        shard_dir.mkdir(parents=True)
        (shard_dir / ".env.sample").write_text("ALPHA_API_KEY=sk-...\n")

        monkeypatch.setattr("ccbench.paths.FORGE", forge_dir)
        monkeypatch.setattr("ccbench.paths.CCBENCH_HOME", home_dir)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("getpass.getpass", lambda _prompt: "secret-value")
        monkeypatch.setattr("builtins.input", lambda _prompt: "")

        secrets = preflight_config_secrets(
            {"configs": ["alpha"]},
            [("", [])],
            {},
        )

        assert secrets == {"ALPHA_API_KEY": "secret-value"}
        secret_file = home_dir / "secrets" / "alpha.env"
        assert secret_file.exists()
        assert "export ALPHA_API_KEY=secret-value" in secret_file.read_text()
        assert secret_file.stat().st_mode & 0o777 == 0o600

    def test_run_experiment_applies_saved_secret_before_staging(
        self, tmp_path, monkeypatch
    ):
        """Resolved secrets are available to config staging before shard copy."""
        tasks_dir = tmp_path / "tasks"
        forge_dir = tmp_path / "config_forge"
        evals_dir = tmp_path / "evals"
        results_dir = tmp_path / "results"
        home_dir = tmp_path / "home"

        task_dir = tasks_dir / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "run.sh").write_text("#!/bin/bash\necho task\n")

        shard_dir = forge_dir / "alpha"
        shard_dir.mkdir(parents=True)
        (shard_dir / ".env.sample").write_text("ALPHA_API_KEY=sk-...\n")
        (shard_dir / "staging.sh").write_text(
            "#!/bin/bash\necho \"$ALPHA_API_KEY\" > staged-secret.txt\n"
        )
        (shard_dir / "run.sh").write_text("#!/bin/bash\necho alpha\n")

        secret_dir = home_dir / "secrets"
        secret_dir.mkdir(parents=True)
        (secret_dir / "alpha.env").write_text("export ALPHA_API_KEY=saved-secret\n")

        monkeypatch.setattr("ccbench.paths.TASKS", tasks_dir)
        monkeypatch.setattr("ccbench.paths.FORGE", forge_dir)
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)
        monkeypatch.setattr("ccbench.paths.RESULTS", results_dir)
        monkeypatch.setattr("ccbench.paths.CCBENCH_HOME", home_dir)

        experiment_root = run_experiment(
            shards=("alpha",),
            task="demo",
            evals=(),
            skip_run=True,
        )

        task_root = experiment_root / "tasks" / "demo"
        assert (task_root / "staged-secret.txt").read_text().strip() == "saved-secret"
        task_env = (task_root / ".env").read_text()
        assert "# === From: ccbench secrets ===" in task_env
        assert "ALPHA_API_KEY=saved-secret" in task_env

    def test_missing_secret_fails_before_creating_result_root(self, tmp_path, monkeypatch):
        """Non-interactive runs fail in preflight without partial result directories."""
        tasks_dir = tmp_path / "tasks"
        forge_dir = tmp_path / "config_forge"
        evals_dir = tmp_path / "evals"
        results_dir = tmp_path / "results"
        home_dir = tmp_path / "home"

        task_dir = tasks_dir / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "run.sh").write_text("#!/bin/bash\necho task\n")

        shard_dir = forge_dir / "alpha"
        shard_dir.mkdir(parents=True)
        (shard_dir / ".env.sample").write_text("ALPHA_API_KEY=sk-...\n")

        monkeypatch.setattr("ccbench.paths.TASKS", tasks_dir)
        monkeypatch.setattr("ccbench.paths.FORGE", forge_dir)
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)
        monkeypatch.setattr("ccbench.paths.RESULTS", results_dir)
        monkeypatch.setattr("ccbench.paths.CCBENCH_HOME", home_dir)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        with pytest.raises(SystemExit) as excinfo:
            run_experiment(
                shards=("alpha",),
                task="demo",
                evals=(),
                skip_run=True,
            )

        assert "Missing required config shard secrets: alpha:ALPHA_API_KEY" in str(
            excinfo.value
        )
        assert not results_dir.exists()

    def test_variant_env_override_only_satisfies_that_variant(
        self, tmp_path, monkeypatch
    ):
        """A secret override in one variant must not hide another variant's missing secret."""
        forge_dir = tmp_path / "config_forge"
        home_dir = tmp_path / "home"
        shard_dir = forge_dir / "alpha"
        shard_dir.mkdir(parents=True)
        (shard_dir / ".env.sample").write_text("ALPHA_API_KEY=sk-...\n")

        monkeypatch.setattr("ccbench.paths.FORGE", forge_dir)
        monkeypatch.setattr("ccbench.paths.CCBENCH_HOME", home_dir)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        with pytest.raises(SystemExit) as excinfo:
            preflight_config_secrets(
                {
                    "variants": {
                        "has_secret": [
                            {"alpha": {"env": {"ALPHA_API_KEY": "variant-secret"}}}
                        ],
                        "missing_secret": ["alpha"],
                    },
                },
                [
                    (
                        "has_secret",
                        [{"alpha": {"env": {"ALPHA_API_KEY": "variant-secret"}}}],
                    ),
                    ("missing_secret", ["alpha"]),
                ],
                {},
            )

        assert "alpha:ALPHA_API_KEY" in str(excinfo.value)


_runner = CliRunner()


class TestBuildApp:
    """Tests for Typer CLI argument parsing with subcommands."""

    def test_old_style_invocation_maps_to_run(self):
        """Passing experiment name directly is preprocessed to the run subcommand."""
        assert _preprocess_tokens(["simple.yaml"]) == ["run", "simple.yaml"]

    def test_old_style_option_first_invocation_maps_to_run(self):
        """Run options before the experiment are still routed to the run command."""
        assert _preprocess_tokens(["--skip-run", "simple.yaml"]) == [
            "run",
            "--skip-run",
            "simple.yaml",
        ]

    def test_explicit_run_subcommand(self):
        with patch("ccbench.cli.run_experiment") as mock:
            result = _runner.invoke(app, ["run", "simple.yaml"])
            assert result.exit_code == 0
            mock.assert_called_once_with(
                "simple.yaml",
                shards=(),
                evals=(),
                variant=None,
                task=None,
                skip_run=False,
                results_dir=None,
            )

    def test_ad_hoc_run_subcommand(self):
        with patch("ccbench.cli.run_experiment") as mock:
            result = _runner.invoke(
                app,
                [
                    "run",
                    "--shard",
                    "claude_code",
                    "--shard",
                    "cc_caveman",
                    "--task",
                    "aoc_2025_10",
                    "--eval",
                    "cloc",
                    "--eval",
                    "claude_code_metrics",
                ],
            )
            assert result.exit_code == 0
            mock.assert_called_once_with(
                None,
                shards=("claude_code", "cc_caveman"),
                evals=("cloc", "claude_code_metrics"),
                variant=None,
                task="aoc_2025_10",
                skip_run=False,
                results_dir=None,
            )

    def test_compare_parses_result_dirs(self):
        with patch("ccbench.cli.cmd_compare") as mock:
            result = _runner.invoke(app, ["compare", "dir1", "dir2"])
            assert result.exit_code == 0
            mock.assert_called_once_with(["dir1", "dir2"], across=False, json_output=False)

    def test_compare_across_flag(self):
        with patch("ccbench.cli.cmd_compare") as mock:
            result = _runner.invoke(app, ["compare", "--across", "dir1", "dir2"])
            assert result.exit_code == 0
            _, kwargs = mock.call_args
            assert kwargs["across"] is True

    def test_compare_json_flag(self):
        with patch("ccbench.cli.cmd_compare") as mock:
            result = _runner.invoke(app, ["compare", "--json", "dir1"])
            assert result.exit_code == 0
            _, kwargs = mock.call_args
            assert kwargs["json_output"] is True

    def test_compare_no_args_defaults_to_empty_list(self):
        with patch("ccbench.cli.cmd_compare") as mock:
            result = _runner.invoke(app, ["compare"])
            assert result.exit_code == 0
            args, _ = mock.call_args
            assert args[0] == []

    def test_retry_parses_result_dirs_and_steps(self):
        with patch("ccbench.cli.retry_results") as mock:
            result = _runner.invoke(
                app,
                [
                    "retry",
                    "results/demo",
                    "--step",
                    "run.*.claude_code.sh",
                    "--task",
                    "demo_task",
                ],
            )
            assert result.exit_code == 0
            mock.assert_called_once_with(
                ["results/demo"],
                steps=("run.*.claude_code.sh",),
                tasks=("demo_task",),
            )


class TestAdHocExperiment:
    """Tests for ad-hoc experiment construction."""

    def test_all_available_evals_are_sorted(self, tmp_path, monkeypatch):
        evals_dir = tmp_path / "evals"
        for eval_name in ["static_analysis", "cloc", "claude_code_metrics"]:
            (evals_dir / eval_name).mkdir(parents=True)
        (evals_dir / "README.md").write_text("not an eval shard")
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)

        assert all_available_evals() == [
            "claude_code_metrics",
            "cloc",
            "static_analysis",
        ]

    def test_build_ad_hoc_experiment_config_defaults_to_all_evals(
        self, tmp_path, monkeypatch
    ):
        evals_dir = tmp_path / "evals"
        for eval_name in ["cloc", "claude_code_metrics"]:
            (evals_dir / eval_name).mkdir(parents=True)
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)

        config = build_ad_hoc_experiment_config(
            ("claude_code", "cc_caveman"),
            "aoc_2025_10",
        )

        assert config == {
            "tasks": ["aoc_2025_10"],
            "configs": ["claude_code", "cc_caveman"],
            "evals": ["claude_code_metrics", "cloc"],
        }
        assert (
            build_ad_hoc_experiment_name(config) == "adhoc_aoc_2025_10_claude_code_cc_caveman"
        )

    def test_build_ad_hoc_experiment_config_custom_evals_override_default(
        self, tmp_path, monkeypatch
    ):
        evals_dir = tmp_path / "evals"
        for eval_name in ["cloc", "static_analysis"]:
            (evals_dir / eval_name).mkdir(parents=True)
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)

        config = build_ad_hoc_experiment_config(
            ("claude_code",),
            "aoc_2025_10",
            ("cloc",),
        )

        assert config == {
            "tasks": ["aoc_2025_10"],
            "configs": ["claude_code"],
            "evals": ["cloc"],
        }

    def test_ad_hoc_experiment_requires_task(self):
        with pytest.raises(SystemExit):
            build_ad_hoc_experiment_config(("claude_code",), None)

    def test_ad_hoc_experiment_requires_shard(self):
        with pytest.raises(SystemExit):
            build_ad_hoc_experiment_config((), "aoc_2025_10")

    def test_run_ad_hoc_experiment_writes_generated_yaml(self, tmp_path, monkeypatch):
        tasks_dir = tmp_path / "tasks"
        forge_dir = tmp_path / "config_forge"
        results_dir = tmp_path / "results"
        task_dir = tasks_dir / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "run.sh").write_text("#!/bin/bash\necho task\n")

        for shard_name in ["alpha", "beta"]:
            shard_dir = forge_dir / shard_name
            shard_dir.mkdir(parents=True)
            (shard_dir / f"{shard_name}.txt").write_text(shard_name)
        evals_dir = tmp_path / "evals"
        for eval_name in ["gamma", "delta"]:
            eval_dir = evals_dir / eval_name
            eval_dir.mkdir(parents=True)
            (eval_dir / f"{eval_name}.txt").write_text(eval_name)

        monkeypatch.setattr("ccbench.paths.TASKS", tasks_dir)
        monkeypatch.setattr("ccbench.paths.FORGE", forge_dir)
        monkeypatch.setattr("ccbench.paths.EVALS", evals_dir)
        monkeypatch.setattr("ccbench.paths.RESULTS", results_dir)

        experiment_root = run_experiment(
            shards=("alpha", "beta"),
            task="demo",
            skip_run=True,
        )

        generated_yaml = experiment_root / "adhoc_demo_alpha_beta.yaml"
        assert generated_yaml.exists()
        assert yaml.safe_load(generated_yaml.read_text()) == {
            "tasks": ["demo"],
            "configs": ["alpha", "beta"],
            "evals": ["delta", "gamma"],
        }
        task_root = experiment_root / "tasks" / "demo"
        assert (task_root / "run.002.demo.sh").exists()
        assert (task_root / "alpha.txt").exists()
        assert (task_root / "beta.txt").exists()
        assert (task_root / "delta.txt").exists()
        assert (task_root / "gamma.txt").exists()


class TestRetryCommand:
    """Tests for retrying existing result steps."""

    def make_task_result(self, tmp_path):
        task_dir = tmp_path / "results" / "exp" / "tasks" / "demo"
        task_dir.mkdir(parents=True)
        (task_dir / "project").mkdir()
        return task_dir

    def test_select_retry_scripts_uses_failed_statuses(self, tmp_path):
        task_dir = self.make_task_result(tmp_path)
        failing = task_dir / "run.000.first.sh"
        passing = task_dir / "run.001.second.sh"
        failing.write_text("#!/bin/bash\nexit 1\n")
        passing.write_text("#!/bin/bash\nexit 0\n")
        run_scripts_with_env_propagation("run.*.sh", task_dir, {}, stop_on_failure=False)

        assert select_retry_scripts(task_dir, ()) == [failing]

    def test_select_retry_scripts_matches_explicit_glob(self, tmp_path):
        task_dir = self.make_task_result(tmp_path)
        first = task_dir / "run.000.first.sh"
        second = task_dir / "run.001.second.sh"
        first.write_text("#!/bin/bash\n")
        second.write_text("#!/bin/bash\n")

        assert select_retry_scripts(task_dir, ("run.*.second.sh",)) == [second]

    def test_retry_failed_steps(self, tmp_path):
        task_dir = self.make_task_result(tmp_path)
        marker = task_dir / "retry-count.txt"
        script = task_dir / "run.000.flaky.sh"
        script.write_text(
            "#!/bin/bash\n"
            "count=$(cat retry-count.txt 2>/dev/null || echo 0)\n"
            "count=$((count + 1))\n"
            "echo $count > retry-count.txt\n"
            'if [ "$count" -eq 1 ]; then exit 1; fi\n'
        )
        run_scripts_with_env_propagation("run.*.sh", task_dir, {}, stop_on_failure=False)

        retry([str(tmp_path / "results" / "exp")])

        assert marker.read_text().strip() == "2"
        statuses = load_script_statuses(task_dir)["scripts"]
        assert statuses["run.000.flaky.sh"]["return_code"] == 0
        assert len(statuses["run.000.flaky.sh"]["attempts"]) == 2

    def test_retry_explicit_step_without_status(self, tmp_path):
        task_dir = self.make_task_result(tmp_path)
        marker = task_dir / "manual.txt"
        (task_dir / "run.000.manual.sh").write_text(
            "#!/bin/bash\necho retried > manual.txt\n"
        )

        retry([str(task_dir)], steps=("run.000.manual.sh",))

        assert marker.read_text().strip() == "retried"


class TestMetricExtraction:
    """Tests for eval JSON extractors."""

    def test_extract_from_claude_metrics(self):
        data = {
            "overall": {
                "total_cost_usd": 0.057747,
                "duration_ms": 13360,
                "num_turns": 4,
                "is_error": False,
                "usage": {
                    "input_tokens": 6,
                    "output_tokens": 555,
                    "cache_read_input_tokens": 114530,
                },
                "model_usage": {
                    "model-a": {
                        "inputTokens": 10,
                        "cacheCreationInputTokens": 5,
                        "cacheReadInputTokens": 15,
                        "outputTokens": 30,
                        "costUSD": 0.60,
                    },
                    "model-b": {
                        "inputTokens": 20,
                        "cacheCreationInputTokens": 0,
                        "cacheReadInputTokens": 10,
                        "outputTokens": 30,
                        "costUSD": 0.60,
                    },
                },
            }
        }
        result = extract_from_claude_metrics(data)
        assert result["cost_usd"] == 0.057747
        assert result["input_token_cost_usd"] == pytest.approx(0.30)
        assert result["cache_token_cost_usd"] == pytest.approx(0.30)
        assert result["output_token_cost_usd"] == pytest.approx(0.60)
        assert result["duration_s"] == 13.36
        assert result["turns"] == 4
        assert result["total_tokens"] == 120
        assert result["input_tokens"] == 30
        assert result["cache_tokens"] == 30
        assert result["output_tokens"] == 60
        assert result["is_error"] is False

    def test_extract_from_claude_metrics_allocates_usage_costs_without_model_usage(self):
        data = {
            "overall": {
                "total_cost_usd": 0.50,
                "duration_ms": 1000,
                "num_turns": 1,
                "is_error": False,
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 5,
                    "output_tokens": 30,
                },
            }
        }

        result = extract_from_claude_metrics(data)

        assert result["input_tokens"] == 10
        assert result["cache_tokens"] == 10
        assert result["output_tokens"] == 30
        assert result["total_tokens"] == 50
        assert result["input_token_cost_usd"] == pytest.approx(0.10)
        assert result["cache_token_cost_usd"] == pytest.approx(0.10)
        assert result["output_token_cost_usd"] == pytest.approx(0.30)

    def test_extract_from_cloc(self):
        data = {"SUM": {"code": 9, "nFiles": 1, "blank": 0, "comment": 0}}
        result = extract_from_cloc(data)
        assert result["loc_total"] == 9
        assert result["loc_files"] == 1

    def test_extract_from_test_pass_rate_completed(self):
        data = {
            "status": "completed",
            "pass_rate": 1.0,
            "tests_passed": 4,
            "tests_failed": 0,
            "duration_s": 0.14,
        }
        result = extract_from_test_pass_rate(data)
        assert result["test_pass_rate"] == 1.0
        assert result["tests_passed"] == 4
        assert result["test_duration_s"] == pytest.approx(0.14)

    def test_extract_from_test_pass_rate_skipped(self):
        data = {"status": "skipped", "reason": "no test files found"}
        result = extract_from_test_pass_rate(data)
        assert result["test_pass_rate"] is None
        assert result["tests_passed"] is None
        assert result["test_duration_s"] is None

    def test_extract_from_static_analysis(self):
        data = {"status": "completed", "lint_errors": 2, "lint_warnings": 1}
        result = extract_from_static_analysis(data)
        assert result["lint_errors"] == 2
        assert result["lint_warnings"] == 1

    def test_extract_from_static_analysis_skipped(self):
        data = {"status": "skipped", "reason": "no changed Python files"}
        result = extract_from_static_analysis(data)
        assert result["lint_errors"] is None

    def test_extract_from_judge_scores(self):
        data = {
            "readability": 4,
            "idiomatic_style": 3,
            "error_handling": 5,
            "efficiency": 4,
            "overall_notes": "Good code.",
        }
        result = extract_from_judge_scores(data)
        assert result["judge_readability"] == 4
        assert result["judge_idiomatic"] == 3
        assert result["judge_error_handling"] == 5
        assert result["judge_efficiency"] == 4

    def test_extract_from_exact_answer(self):
        data = {"status": "completed", "matched": True, "score": 1}
        result = extract_from_exact_answer(data)
        assert result["exact_answer_score"] == 1
        assert result["exact_answer_matched"] is True

    def test_extract_metrics_summary_missing_files(self, tmp_path):
        result = extract_metrics_summary(tmp_path)
        assert result == {}

    def test_extract_metrics_summary_with_files(self, tmp_path):
        (tmp_path / "claude_code_metrics.json").write_text(
            json.dumps(
                {
                    "overall": {
                        "total_cost_usd": 0.05,
                        "duration_ms": 10000,
                        "num_turns": 3,
                        "is_error": False,
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 100,
                            "cache_read_input_tokens": 0,
                        },
                    }
                }
            )
        )
        (tmp_path / "cloc.json").write_text(
            json.dumps({"SUM": {"code": 15, "nFiles": 2, "blank": 1, "comment": 0}})
        )
        result = extract_metrics_summary(tmp_path)
        assert result["cost_usd"] == 0.05
        assert result["loc_total"] == 15


class TestExactAnswerEval:
    """Tests for the exact_answer eval shard."""

    expected = "41384454324123574919196129"

    @pytest.mark.parametrize(
        "answer_text",
        [
            "41384454324123574919196129",
            "41,384,454,324,123,574,919,196,129",
            "41 384 454 324 123 574 919 196 129",
            "41_384_454_324_123_574_919_196_129",
            "4.1384454324123574919196129e25",
            "41384454324123574919196128 + 1",
        ],
    )
    def test_check_answer_accepts_equivalent_representations(self, answer_text):
        """Equivalent numeric formats should pass the exact answer check."""
        parser = load_exact_answer_module()
        matched, matched_value = parser.check_answer(answer_text, self.expected)

        assert matched is True
        assert matched_value is not None

    def test_check_answer_rejects_wrong_value(self):
        """Wrong numeric answers should fail the exact answer check."""
        parser = load_exact_answer_module()
        matched, matched_value = parser.check_answer(
            "41384454324123574919196128",
            self.expected,
        )

        assert matched is False
        assert matched_value is None

    def test_collect_output_text_reads_claude_assistant_text(self, tmp_path):
        """Claude JSONL output should be reduced to assistant text blocks."""
        parser = load_exact_answer_module()
        output = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "41,384,454,324,123,574,919,196,129",
                    }
                ]
            },
        }
        (tmp_path / "output.json").write_text(json.dumps(output) + "\n")

        text, sources = parser.collect_output_text(tmp_path)

        assert sources == ["output.json"]
        assert "41,384,454,324,123,574,919,196,129" in text

    def test_collect_output_text_reads_response_text_first(self, tmp_path):
        """Plain llm shard output should be evaluated directly."""
        parser = load_exact_answer_module()
        (tmp_path / "response.txt").write_text("41,384,454,324,123,574,919,196,129\n")
        (tmp_path / "output.json").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "wrong"}]},
                }
            )
            + "\n"
        )

        text, sources = parser.collect_output_text(tmp_path)

        assert sources == ["response.txt", "output.json"]
        assert text.splitlines()[0] == "41,384,454,324,123,574,919,196,129"

    def test_collect_output_text_reads_numeric_response_as_plain_text(self, tmp_path):
        """A bare numeric response file should not be treated as JSON output."""
        parser = load_exact_answer_module()
        (tmp_path / "response.txt").write_text("41384454324123574919196129\n")

        text, sources = parser.collect_output_text(tmp_path)

        assert sources == ["response.txt"]
        assert text.strip() == "41384454324123574919196129"


class TestCompareTable:
    """Tests for table/JSON rendering."""

    def test_render_two_columns(self):
        columns = ["baseline", "variant-a"]
        metrics = [
            {
                "cost_usd": 0.05,
                "input_token_cost_usd": 0.01,
                "cache_token_cost_usd": 0.02,
                "output_token_cost_usd": 0.02,
                "duration_s": 10.0,
                "turns": 4,
                "total_tokens": 130,
                "input_tokens": 10,
                "cache_tokens": 20,
                "output_tokens": 100,
                "loc_total": 9,
                "test_pass_rate": 0.5,
            },
            {
                "cost_usd": 0.30,
                "input_token_cost_usd": 0.12,
                "cache_token_cost_usd": 0.06,
                "output_token_cost_usd": 0.12,
                "duration_s": 5.0,
                "turns": 13,
                "total_tokens": 168,
                "input_tokens": 12,
                "cache_tokens": 6,
                "output_tokens": 150,
                "loc_total": 7,
                "test_pass_rate": 1.0,
            },
        ]
        table = render_comparison_table(columns, metrics)
        assert "baseline" in table
        assert "variant-a" in table
        assert "$0.0500" in table
        assert "$0.3000 (+500%)" in table
        assert "Input token cost (USD)" in table
        assert "$0.1200" in table
        assert "168 (+29.2%)" in table
        assert "Cache tokens" in table
        assert "5.0 (-50%)" in table
        assert "150 (+50%)" in table
        assert "7 (-22.2%)" in table
        assert "4" in table
        assert "13" in table
        assert "100% (+100%)" not in table

    def test_missing_values_show_dash(self):
        columns = ["a", "b"]
        metrics = [
            {"cost_usd": 0.05, "test_pass_rate": None},
            {"cost_usd": 0.10, "test_pass_rate": 1.0},
        ]
        table = render_comparison_table(columns, metrics)
        assert "\u2014" in table  # em dash for missing value
        assert "100%" in table

    def test_rows_with_all_none_hidden(self):
        columns = ["a", "b"]
        metrics = [
            {"cost_usd": 0.05, "judge_readability": None},
            {"cost_usd": 0.10, "judge_readability": None},
        ]
        table = render_comparison_table(columns, metrics)
        assert "Readability" not in table

    def test_no_metrics_message(self):
        table = render_comparison_table(["a"], [{}])
        assert "No metrics found" in table

    def test_json_output_structure(self):
        columns = ["baseline", "variant"]
        metrics = [
            {"cost_usd": 0.05, "turns": 4, "total_tokens": 100},
            {"cost_usd": 0.30, "turns": 13, "total_tokens": 125},
        ]
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)
        assert data["variants"] == ["baseline", "variant"]
        assert data["metrics"]["cost_usd"] == [0.05, 0.30]
        assert data["metrics"]["total_tokens"] == [100, 125]
        assert data["metrics"]["turns"] == [4, 13]
        assert data["metric_changes_pct"]["cost_usd"] == [None, 500.0]
        assert data["metric_changes_pct"]["total_tokens"] == [None, 25.0]
        assert "turns" not in data["metric_changes_pct"]

    def test_percent_changes_use_matching_task_baseline(self):
        columns = [
            "task_a_baseline",
            "task_a_variant",
            "task_b_baseline",
            "task_b_variant",
        ]
        metrics = [
            {"cost_usd": 10.0},
            {"cost_usd": 15.0},
            {"cost_usd": 100.0},
            {"cost_usd": 50.0},
        ]

        table = render_comparison_table(columns, metrics)
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)

        assert "$15.0000 (+50%)" in table
        assert "$50.0000 (-50%)" in table
        assert "$100.0000 (+900%)" not in table
        assert data["metric_changes_pct"]["cost_usd"] == [None, 50.0, None, -50.0]

    def test_baseline_variant_can_appear_after_other_variants(self):
        columns = ["task_openspec", "task_baseline"]
        metrics = [
            {"duration_s": 30.0},
            {"duration_s": 10.0},
        ]

        table = render_comparison_table(columns, metrics)
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)

        assert "30.0 (+200%)" in table
        assert "10.0 (+200%)" not in table
        assert data["metric_changes_pct"]["duration_s"] == [200.0, None]

    def test_first_variant_is_reference_when_no_baseline_exists(self):
        columns = ["task_openspec", "task_bmad"]
        metrics = [
            {"output_tokens": 100},
            {"output_tokens": 150},
        ]

        table = render_comparison_table(columns, metrics)
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)

        assert "150 (+50%)" in table
        assert data["metric_changes_pct"]["output_tokens"] == [None, 50.0]

    def test_experiment_metadata_prevents_comparing_different_tasks(self, tmp_path):
        result_root = tmp_path / "run"
        tasks_dir = result_root / "tasks"
        task_a = tasks_dir / "aoc_2025_01"
        task_b = tasks_dir / "aoc_2025_02"
        task_a.mkdir(parents=True)
        task_b.mkdir()
        (result_root / "simple.yaml").write_text(
            yaml.safe_dump(
                {
                    "tasks": ["aoc_2025_01", "aoc_2025_02"],
                    "configs": ["claude_code"],
                }
            )
        )

        reference_indices = reference_indices_for_task_entries(
            [("aoc_2025_01", task_a), ("aoc_2025_02", task_b)]
        )
        raw = render_comparison_json(
            ["aoc_2025_01", "aoc_2025_02"],
            [{"cost_usd": 1.0}, {"cost_usd": 2.0}],
            reference_indices,
        )

        assert reference_indices == [None, None]
        assert "metric_changes_pct" not in json.loads(raw)

    def test_experiment_metadata_groups_variants_by_task(self, tmp_path):
        result_root = tmp_path / "run"
        tasks_dir = result_root / "tasks"
        paths = [
            tasks_dir / "fair-share_openspec",
            tasks_dir / "fair-share_baseline",
            tasks_dir / "c4-stop-button_baseline",
            tasks_dir / "c4-stop-button_openspec",
        ]
        for path in paths:
            path.mkdir(parents=True)
        (result_root / "spec-driven-comparison.yaml").write_text(
            yaml.safe_dump(
                {
                    "tasks": ["fair-share", "c4-stop-button"],
                    "variants": {"baseline": ["claude_code"], "openspec": ["openspec"]},
                }
            )
        )

        reference_indices = reference_indices_for_task_entries(
            [(path.name, path) for path in paths]
        )

        assert reference_indices == [1, None, None, 2]

    def test_build_task_results_aggregates_matching_task_variant(self, tmp_path):
        run_a = tmp_path / "run-a"
        run_b = tmp_path / "run-b"
        task_a = run_a / "tasks" / "task_baseline"
        task_b = run_b / "tasks" / "task_baseline"
        task_a.mkdir(parents=True)
        task_b.mkdir(parents=True)
        for task_dir, cost, duration in [
            (task_a, 0.10, 10_000),
            (task_b, 0.30, 30_000),
        ]:
            (task_dir / "claude_code_metrics.json").write_text(
                json.dumps(
                    {
                        "overall": {
                            "total_cost_usd": cost,
                            "duration_ms": duration,
                            "num_turns": 4,
                            "is_error": False,
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 100,
                                "cache_read_input_tokens": 0,
                            },
                        }
                    }
                )
            )

        task_results = build_task_results(
            [
                ("run-a/task_baseline", task_a),
                ("run-b/task_baseline", task_b),
            ]
        )

        assert list(task_results) == ["task"]
        assert task_results["task"]["variants"] == ["baseline"]
        assert task_results["task"]["sample_counts"] == {"baseline": 2}
        metrics = task_results["task"]["metrics"][0]
        assert metrics["cost_usd"] == 0.2
        assert metrics["duration_s"] == 20
        assert metrics["turns"] == 4
        assert metrics["total_tokens"] == 105
        assert metrics["input_tokens"] == 5
        assert metrics["cache_tokens"] == 0
        assert metrics["output_tokens"] == 100
        assert metrics["input_token_cost_usd"] == pytest.approx(0.0095238095)
        assert metrics["cache_token_cost_usd"] == 0
        assert metrics["output_token_cost_usd"] == pytest.approx(0.1904761905)
        assert task_results["task"]["metric_stats"][0]["cost_usd"] == {
            "mean": 0.2,
            "min": 0.1,
            "max": 0.3,
            "count": 2,
        }
        assert task_results["task"]["metric_stats"][0]["duration_s"] == {
            "mean": 20,
            "min": 10.0,
            "max": 30.0,
            "count": 2,
        }

    def test_grouped_table_headers_include_sample_counts(self, tmp_path):
        run_a = tmp_path / "run-a"
        run_b = tmp_path / "run-b"
        variant_a = run_a / "tasks" / "task_baseline"
        variant_b = run_b / "tasks" / "task_baseline"
        variant_c = run_a / "tasks" / "task_variant"
        for path, cost in [(variant_a, 0.10), (variant_b, 0.30), (variant_c, 0.40)]:
            path.mkdir(parents=True)
            (path / "claude_code_metrics.json").write_text(
                json.dumps(
                    {
                        "overall": {
                            "total_cost_usd": cost,
                            "duration_ms": 10000,
                            "num_turns": 4,
                            "is_error": False,
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 100,
                                "cache_read_input_tokens": 0,
                            },
                        }
                    }
                )
            )

        task_results = build_task_results(
            [
                ("run-a/task_baseline", variant_a),
                ("run-b/task_baseline", variant_b),
                ("run-a/task_variant", variant_c),
            ]
        )

        table = render_grouped_comparison_table(task_results)
        assert "baseline (n=2)" in table
        assert "variant (n=1)" in table
        assert "$0.2000 [$0.1000..$0.3000]" in table
        assert "$0.4000 (+100%)" in table

    def test_grouped_compare_uses_first_variant_when_no_baseline_exists(self, tmp_path):
        task_a = tmp_path / "tasks" / "task_openspec"
        task_b = tmp_path / "tasks" / "task_bmad"
        for path, cost in [(task_a, 1.0), (task_b, 2.0)]:
            path.mkdir(parents=True)
            (path / "claude_code_metrics.json").write_text(
                json.dumps(
                    {
                        "overall": {
                            "total_cost_usd": cost,
                            "duration_ms": 10000,
                            "num_turns": 4,
                            "is_error": False,
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 100,
                                "cache_read_input_tokens": 0,
                            },
                        }
                    }
                )
            )

        task_results = build_task_results(
            [
                ("task_openspec", task_a),
                ("task_bmad", task_b),
            ]
        )
        table = render_grouped_comparison_table(task_results)
        data = json.loads(render_grouped_comparison_json(task_results))

        assert "$2.0000 (+100%)" in table
        assert data["tasks"]["task"]["metric_changes_pct"]["cost_usd"] == [None, 100.0]

    def test_percent_change_skips_zero_baseline(self):
        columns = ["baseline", "variant"]
        metrics = [
            {"cost_usd": 0, "duration_s": 0},
            {"cost_usd": 1.0, "duration_s": 5.0},
        ]

        table = render_comparison_table(columns, metrics)
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)

        assert "$1.0000 (" not in table
        assert "5.0 (" not in table
        assert data["metric_changes_pct"]["cost_usd"] == [None, None]
        assert data["metric_changes_pct"]["duration_s"] == [None, None]


class TestResolveTaskDirs:
    """Tests for directory resolution in compare command."""

    def test_experiment_dir_expands_children(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task_baseline").mkdir()
        (tasks_dir / "task_variant").mkdir()

        entries = resolve_task_dirs([str(tmp_path)], across=False)
        labels = [label for label, _ in entries]
        assert "task_baseline" in labels
        assert "task_variant" in labels
        assert len(entries) == 2

    def test_task_variant_dir_directly(self, tmp_path):
        (tmp_path / "project").mkdir()
        (tmp_path / "output.json").write_text("{}")

        entries = resolve_task_dirs([str(tmp_path)], across=False)
        assert len(entries) == 1
        assert entries[0][1] == tmp_path

    def test_nonexistent_dir_skipped(self, tmp_path):
        entries = resolve_task_dirs([str(tmp_path / "nonexistent")], across=False)
        assert len(entries) == 0

    def test_across_labels_include_parent(self, tmp_path):
        run1 = tmp_path / "run1"
        tasks1 = run1 / "tasks"
        tasks1.mkdir(parents=True)
        (tasks1 / "task_a").mkdir()

        entries = resolve_task_dirs([str(run1)], across=True)
        assert entries[0][0] == "run1/task_a"


class TestCompareCommand:
    """Integration tests for the full compare flow."""

    def _make_result_dir(self, tmp_path):
        """Create a mock result directory with two variants."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()

        for name, cost, loc in [("task_baseline", 0.05, 9), ("task_variant", 0.30, 7)]:
            d = tasks / name
            d.mkdir()
            (d / "claude_code_metrics.json").write_text(
                json.dumps(
                    {
                        "overall": {
                            "total_cost_usd": cost,
                            "duration_ms": 10000,
                            "num_turns": 4,
                            "is_error": False,
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 100,
                                "cache_read_input_tokens": 0,
                            },
                        }
                    }
                )
            )
            (d / "cloc.json").write_text(
                json.dumps({"SUM": {"code": loc, "nFiles": 1, "blank": 0, "comment": 0}})
            )

        return tmp_path

    def test_compare_single_run(self, tmp_path):
        result_dir = self._make_result_dir(tmp_path)
        result = subprocess.run(
            ["uv", "run", "ccbench", "compare", str(result_dir)],
            capture_output=True,
            text=True,
            cwd=CCBENCH_DIR,
        )
        assert result.returncode == 0
        assert "Task: task" in result.stdout
        assert "baseline (n=1)" in result.stdout
        assert "variant (n=1)" in result.stdout
        assert "$0.0500" in result.stdout

    def test_compare_outputs_separate_table_per_task(self, tmp_path):
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        for name, cost in [
            ("fair-share_baseline", 0.10),
            ("fair-share_variant", 0.20),
            ("debug_baseline", 1.00),
            ("debug_variant", 1.50),
        ]:
            task_dir = tasks / name
            task_dir.mkdir()
            (task_dir / "claude_code_metrics.json").write_text(
                json.dumps(
                    {
                        "overall": {
                            "total_cost_usd": cost,
                            "duration_ms": 10000,
                            "num_turns": 4,
                            "is_error": False,
                            "usage": {
                                "input_tokens": 5,
                                "output_tokens": 100,
                                "cache_read_input_tokens": 0,
                            },
                        }
                    }
                )
            )

        result = subprocess.run(
            ["uv", "run", "ccbench", "compare", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=CCBENCH_DIR,
        )

        assert result.returncode == 0
        assert "Task: fair-share" in result.stdout
        assert "Task: debug" in result.stdout
        assert "baseline (n=1)" in result.stdout
        assert "variant (n=1)" in result.stdout
        assert "$0.2000 (+100%)" in result.stdout
        assert "$1.5000 (+50%)" in result.stdout

    def test_compare_json_output(self, tmp_path):
        result_dir = self._make_result_dir(tmp_path)
        result = subprocess.run(
            ["uv", "run", "ccbench", "compare", "--json", str(result_dir)],
            capture_output=True,
            text=True,
            cwd=CCBENCH_DIR,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["tasks"]["task"]["variants"] == ["baseline", "variant"]
        assert data["tasks"]["task"]["metrics"]["cost_usd"] == [0.05, 0.30]
        assert data["tasks"]["task"]["sample_counts"] == {"baseline": 1, "variant": 1}
        assert data["tasks"]["task"]["metric_stats"]["cost_usd"] == [
            {"mean": 0.05, "min": 0.05, "max": 0.05, "count": 1},
            {"mean": 0.30, "min": 0.30, "max": 0.30, "count": 1},
        ]

    def test_compare_nonexistent_dir(self, tmp_path):
        result = subprocess.run(
            ["uv", "run", "ccbench", "compare", str(tmp_path / "nope")],
            capture_output=True,
            text=True,
            cwd=CCBENCH_DIR,
        )
        assert result.returncode != 0

    def test_compare_defaults_to_most_recent(self, tmp_path, monkeypatch):
        """With no args, compare picks the lexicographically last result dir."""
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        # Create two timestamped result dirs; the second is "newer"
        for name in ["20250101_000000_old", "20250202_000000_new"]:
            run_dir = results_dir / name
            run_dir.mkdir()
            self._make_result_dir(run_dir)
        monkeypatch.setattr("ccbench.paths.RESULTS", results_dir)
        import io
        from contextlib import redirect_stdout

        from ccbench.compare import cmd_compare

        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compare(json_output=True)
        data = json.loads(buf.getvalue())
        # Should have resolved the most recent dir's task variants
        assert data["tasks"]["task"]["variants"] == ["baseline", "variant"]
