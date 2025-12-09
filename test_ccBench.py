"""Tests for ccBench file merging functionality."""

import json
import tomllib

import pytest
import tomli_w

from ccBench import copy_file_with_json_merge, deep_merge_dict


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


class TestCopyFileWithJsonMerge:
    """Tests for copy_file_with_json_merge function."""

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

        copy_file_with_json_merge(source_file, temp_target)

        target_file = temp_target / "test.txt"
        assert target_file.exists()
        assert target_file.read_text() == "content"

    def test_copy_json_no_conflict(self, temp_source, temp_target):
        """Test copying JSON file when target doesn't exist."""
        source_file = temp_source / "config.json"
        data = {"key": "value"}
        source_file.write_text(json.dumps(data))

        copy_file_with_json_merge(source_file, temp_target)

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

        copy_file_with_json_merge(source_file, temp_target)

        # Verify merge
        result = json.loads(target_file.read_text())
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_copy_toml_no_conflict(self, temp_source, temp_target):
        """Test copying TOML file when target doesn't exist."""
        source_file = temp_source / "config.toml"
        data = {"key": "value"}
        with source_file.open("wb") as f:
            tomli_w.dump(data, f)

        copy_file_with_json_merge(source_file, temp_target)

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

        copy_file_with_json_merge(source_file, temp_target)

        # Verify merge
        with target_file.open("rb") as f:
            result = tomllib.load(f)
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_copy_directory_empty(self, temp_source, temp_target):
        """Test copying an empty directory."""
        source_dir = temp_source / "empty_dir"
        source_dir.mkdir()

        copy_file_with_json_merge(source_dir, temp_target)

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

        copy_file_with_json_merge(source_dir, temp_target)

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

        copy_file_with_json_merge(source_dir, temp_target)

        # Verify merge
        result = json.loads(target_json.read_text())
        assert result == {"existing": "value", "new": "value"}

    def test_copy_nested_directories(self, temp_source, temp_target):
        """Test copying nested directory structures."""
        source_dir = temp_source / "parent"
        source_dir.mkdir()
        child_dir = source_dir / "child"
        child_dir.mkdir()
        (child_dir / "file.txt").write_text("nested")

        copy_file_with_json_merge(source_dir, temp_target)

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

        copy_file_with_json_merge(source_file, temp_target)

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

        copy_file_with_json_merge(source_file, temp_target)

        assert target_file.read_text() == "new content"

    def test_invalid_json_fallback(self, temp_source, temp_target):
        """Test that invalid JSON falls back to overwrite."""
        # Create target with valid JSON
        target_file = temp_target / "config.json"
        target_file.write_text('{"key": "value"}')

        # Create source with invalid JSON
        source_file = temp_source / "config.json"
        source_file.write_text("not valid json{")

        copy_file_with_json_merge(source_file, temp_target)

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

        copy_file_with_json_merge(source_file, temp_target)

        # Should overwrite with invalid content
        assert target_file.read_text() == "not valid toml [[["
