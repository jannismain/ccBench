"""Tests for ccBench file merging functionality."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import tomli_w

from ccBench import (
    CCBENCH_DIR,
    CCBENCH_IGNORE,
    STAGING_SCRIPT,
    apply_shard_env,
    build_parser,
    copy_item,
    copy_shard_with_script_rename,
    copy_task_shard_first,
    deep_merge_dict,
    ensure_project_git_repo,
    extract_from_claude_metrics,
    extract_from_cloc,
    extract_from_test_pass_rate,
    extract_from_static_analysis,
    extract_from_judge_scores,
    extract_metrics_summary,
    parse_shard_entry,
    process_shard,
    render_comparison_json,
    render_comparison_table,
    resolve_task_dirs,
    run_scripts_with_env_propagation,
)


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
        assert self.run_git(["log", "--format=%s", "-1"], project_dir) == "before experiment"

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
            ["uv", "run", "python", "ccBench.py", "test_model_override", "--skip-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "CCBENCH_RESULT": str(tmp_path)},
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
            l for l in sonnet_env.splitlines() if l.startswith("ANTHROPIC_MODEL=")
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


class TestBuildParser:
    """Tests for CLI argument parsing with subcommands."""

    def test_old_style_invocation_maps_to_run(self):
        """Passing experiment name directly still works via sys.argv insertion."""
        import sys

        original = sys.argv[:]
        try:
            sys.argv = ["ccBench.py", "simple.yaml"]
            known_commands = {"run", "compare"}
            if len(sys.argv) > 1 and sys.argv[1] not in known_commands and not sys.argv[1].startswith("-"):
                sys.argv.insert(1, "run")
            parser = build_parser()
            args = parser.parse_args()
            assert args.command == "run"
            assert args.experiment == "simple.yaml"
        finally:
            sys.argv = original

    def test_explicit_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["run", "simple.yaml"])
        assert args.command == "run"
        assert args.experiment == "simple.yaml"

    def test_compare_parses_result_dirs(self):
        parser = build_parser()
        args = parser.parse_args(["compare", "dir1", "dir2"])
        assert args.command == "compare"
        assert args.result_dirs == ["dir1", "dir2"]

    def test_compare_across_flag(self):
        parser = build_parser()
        args = parser.parse_args(["compare", "--across", "dir1", "dir2"])
        assert args.across is True

    def test_compare_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["compare", "--json", "dir1"])
        assert args.json_output is True

    def test_compare_no_args_defaults_to_empty_list(self):
        parser = build_parser()
        args = parser.parse_args(["compare"])
        assert args.result_dirs == []


class TestParsePytest:
    """Tests for pytest log parsing (used by test_pass_rate eval)."""

    def test_strip_ansi(self):
        from evals.test_pass_rate.parse_pytest import strip_ansi

        text = "\x1b[32m4 passed\x1b[0m in 0.14s"
        assert strip_ansi(text) == "4 passed in 0.14s"

    def test_parse_all_passed(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        log = "======== 4 passed in 0.14s ========"
        result = parse_pytest_summary(log)
        assert result is not None
        assert result["tests_passed"] == 4
        assert result["tests_failed"] == 0
        assert result["tests_run"] == 4
        assert result["pass_rate"] == 1.0
        assert result["duration_s"] == pytest.approx(0.14)

    def test_parse_mixed_results(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        log = "======== 2 passed, 1 failed in 0.5s ========"
        result = parse_pytest_summary(log)
        assert result is not None
        assert result["tests_passed"] == 2
        assert result["tests_failed"] == 1
        assert result["tests_run"] == 3
        assert result["duration_s"] == pytest.approx(0.5)

    def test_parse_with_errors_and_skipped(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        log = "======== 3 passed, 1 failed, 2 errors, 1 skipped in 1.2s ========"
        result = parse_pytest_summary(log)
        assert result is not None
        assert result["tests_passed"] == 3
        assert result["tests_failed"] == 3  # 1 failed + 2 errors
        assert result["tests_skipped"] == 1
        assert result["tests_run"] == 7

    def test_parse_duration_with_minutes(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        log = "======== 10 passed in 1m 3.45s ========"
        result = parse_pytest_summary(log)
        assert result is not None
        assert result["duration_s"] == pytest.approx(63.45)

    def test_parse_no_pytest_output(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        log = "just some random log output\nnothing to see here"
        assert parse_pytest_summary(log) is None

    def test_parse_with_ansi_codes(self):
        from evals.test_pass_rate.parse_pytest import parse_pytest_summary

        # Real pytest output has ANSI codes around the summary
        log = "\x1b[32m========== \x1b[32m\x1b[1m4 passed\x1b[0m\x1b[32m in 0.14s\x1b[0m\x1b[32m ===========\x1b[0m"
        result = parse_pytest_summary(log)
        assert result is not None
        assert result["tests_passed"] == 4

    def test_extract_failures(self):
        from evals.test_pass_rate.parse_pytest import extract_failures

        log = "FAILED test_solution.py::test_big_numbers - AssertionError\nFAILED test_solution.py::test_edge"
        failures = extract_failures(log)
        assert len(failures) == 2
        assert "test_solution.py::test_big_numbers" in failures


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
            }
        }
        result = extract_from_claude_metrics(data)
        assert result["cost_usd"] == 0.057747
        assert result["duration_s"] == 13.36
        assert result["turns"] == 4
        assert result["output_tokens"] == 555
        assert result["is_error"] is False

    def test_extract_from_cloc(self):
        data = {"SUM": {"code": 9, "nFiles": 1, "blank": 0, "comment": 0}}
        result = extract_from_cloc(data)
        assert result["loc_total"] == 9
        assert result["loc_files"] == 1

    def test_extract_from_test_pass_rate_completed(self):
        data = {"status": "completed", "pass_rate": 1.0, "tests_passed": 4, "tests_failed": 0, "duration_s": 0.14}
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

    def test_extract_metrics_summary_missing_files(self, tmp_path):
        result = extract_metrics_summary(tmp_path)
        assert result == {}

    def test_extract_metrics_summary_with_files(self, tmp_path):
        (tmp_path / "claude_code_metrics.json").write_text(json.dumps({
            "overall": {
                "total_cost_usd": 0.05,
                "duration_ms": 10000,
                "num_turns": 3,
                "is_error": False,
                "usage": {"input_tokens": 5, "output_tokens": 100, "cache_read_input_tokens": 0},
            }
        }))
        (tmp_path / "cloc.json").write_text(json.dumps({
            "SUM": {"code": 15, "nFiles": 2, "blank": 1, "comment": 0}
        }))
        result = extract_metrics_summary(tmp_path)
        assert result["cost_usd"] == 0.05
        assert result["loc_total"] == 15


class TestCompareTable:
    """Tests for table/JSON rendering."""

    def test_render_two_columns(self):
        columns = ["baseline", "variant-a"]
        metrics = [
            {"cost_usd": 0.05, "turns": 4},
            {"cost_usd": 0.30, "turns": 13},
        ]
        table = render_comparison_table(columns, metrics)
        assert "baseline" in table
        assert "variant-a" in table
        assert "$0.0500" in table
        assert "$0.3000" in table
        assert "4" in table
        assert "13" in table

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
            {"cost_usd": 0.05, "turns": 4},
            {"cost_usd": 0.30, "turns": 13},
        ]
        raw = render_comparison_json(columns, metrics)
        data = json.loads(raw)
        assert data["variants"] == ["baseline", "variant"]
        assert data["metrics"]["cost_usd"] == [0.05, 0.30]
        assert data["metrics"]["turns"] == [4, 13]


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
            (d / "claude_code_metrics.json").write_text(json.dumps({
                "overall": {
                    "total_cost_usd": cost,
                    "duration_ms": 10000,
                    "num_turns": 4,
                    "is_error": False,
                    "usage": {"input_tokens": 5, "output_tokens": 100, "cache_read_input_tokens": 0},
                }
            }))
            (d / "cloc.json").write_text(json.dumps({
                "SUM": {"code": loc, "nFiles": 1, "blank": 0, "comment": 0}
            }))

        return tmp_path

    def test_compare_single_run(self, tmp_path):
        result_dir = self._make_result_dir(tmp_path)
        result = subprocess.run(
            ["uv", "run", "python", "ccBench.py", "compare", str(result_dir)],
            capture_output=True, text=True, cwd=CCBENCH_DIR,
        )
        assert result.returncode == 0
        assert "task_baseline" in result.stdout
        assert "task_variant" in result.stdout
        assert "$0.0500" in result.stdout

    def test_compare_json_output(self, tmp_path):
        result_dir = self._make_result_dir(tmp_path)
        result = subprocess.run(
            ["uv", "run", "python", "ccBench.py", "compare", "--json", str(result_dir)],
            capture_output=True, text=True, cwd=CCBENCH_DIR,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "variants" in data
        assert "metrics" in data
        assert "cost_usd" in data["metrics"]

    def test_compare_nonexistent_dir(self, tmp_path):
        result = subprocess.run(
            ["uv", "run", "python", "ccBench.py", "compare", str(tmp_path / "nope")],
            capture_output=True, text=True, cwd=CCBENCH_DIR,
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
        monkeypatch.setattr("ccBench.RESULTS", results_dir)
        from ccBench import cmd_compare, build_parser
        args = build_parser().parse_args(["compare", "--json"])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compare(args)
        data = json.loads(buf.getvalue())
        # Should have resolved the most recent dir's task variants
        assert "variants" in data
        assert len(data["variants"]) == 2
