# Developer Guide for ccBench

This document provides guidelines for developers working on ccBench itself.

## Testing

### Running Tests

Run the local quality gate before handing off changes:

```bash
uv run pytest -q
uv run ruff check .
uv run ty check
```

Pytest discovery is scoped in `pyproject.toml` so benchmark task fixture tests under `tasks/` are not collected as repo tests.

### Writing Tests

When adding new functionality to ccBench, follow these testing guidelines:

#### Test Structure

- Use **pytest** with class-based organization
- Group related tests into test classes (e.g., `TestDeepMergeDict`, `TestCopyItem`)
- Write descriptive test names that explain what is being tested
- Add docstrings to explain the test's purpose
- Use pytest fixtures for common setup/teardown (e.g., temporary directories)

#### Test Principles

1. **Minimal but Exhaustive** - Cover all code paths without redundancy
2. **Isolated** - Each test should be independent
3. **Temporary Files Only** - Use `tmp_path` fixture, never write to actual project directories
4. **Clear Assertions** - Use specific assertions with clear failure messages
5. **Prefer Integration Tests** - When real experiment YAMLs and shard directories exist (e.g., `experiments/test_model_override.yaml`), write tests that run the actual CLI against them rather than simulating internal logic in unit tests
6. **CLI Testability** - CLI commands that write output must have configurable output directories (via env var or CLI flag) so tests can redirect to `tmp_path`. Example: `CCBENCH_RESULT`

#### Test Coverage Areas

When testing ccBench functionality, ensure coverage of:

- **Happy paths** - Normal, expected usage
- **Edge cases** - Empty inputs, missing files, etc.
- **Error handling** - Invalid JSON/TOML, permission errors, etc.
- **Integration** - How components work together
- **Real-world scenarios** - Actual use cases (e.g., merging `.Codex/settings.json`)

#### Testing File Operations

When testing file operations:

```python
# ✅ Good - Use fixtures and tmp_path
def test_copy_file(self, temp_source, temp_target):
    source_file = temp_source / "test.txt"
    source_file.write_text("content")
    copy_item(source_file, temp_target)
    assert (temp_target / "test.txt").read_text() == "content"

# ❌ Bad - Don't write to actual directories
def test_copy_file_bad(self):
    source_file = Path("test.txt")
    source_file.write_text("content")  # Creates file in project!
    copy_item(source_file, Path("."))
```

## Code Style

### General Guidelines

- Follow PEP 8 conventions
- Use type hints for function signatures
- Write minimal docstrings for public functions, include only information not obvious from code, e.g.:
  - Purpose of function
  - Explanation of parameters if not clear
  - Return value description
- Omit docstrings, if method and parameter name are self-explanatory
- Use descriptive variable names

### Error Handling

```python
# ✅ Good - Specific exceptions, fallback behavior
try:
    with source.open() as f:
        data = json.load(f)
except (json.JSONDecodeError, KeyError) as e:
    log.warning(f"Failed to parse: {e}. Falling back to copy.")
    source.copy_into(target_dir)

# ❌ Bad - Bare except, no recovery
try:
    data = json.load(f)
except:
    pass
```

## Making Changes

### Adding New Features

1. **Write tests first** - TDD approach ensures testability
2. **Implement feature** - Keep it minimal and focused
3. **Update documentation** - README, USER_GUIDE, or AGENTS.md as appropriate
4. **Run test suite** - Ensure all tests pass
5. **Test manually** - Run actual experiments to verify

### Modifying Merge Behavior

If you need to change how files are merged:

1. Update `deep_merge_dict` function
2. Add test cases for new behavior
3. Update USER_GUIDE.md with merge examples
4. Consider backward compatibility

### Adding New File Types

To support merging new file types (e.g., YAML):

1. Add parsing/writing logic in `copy_item`
2. Add comprehensive tests (merge, error handling, fallback)
3. Document in USER_GUIDE.md
4. Add dependency to `pyproject.toml` if needed

## Debugging

### Debugging Tests

Use pytest's verbose flag and print debugging:

```bash
# Run with maximum verbosity
uv run pytest test_ccBench.py -vv

# Run with print output (pytest captures by default)
uv run pytest test_ccBench.py -s

# Run specific test with debugging
uv run pytest test_ccBench.py::TestClass::test_method -vv -s
```

## Dependencies

### Adding Dependencies

Use `uv add <package>` to add new dependencies. For example, to add PyYAML:

```bash
uv add pyyaml
```

### Development Dependencies

Add development dependencies (e.g., linters, type checkers) with `--dev`:

```sh
uv add --dev ruff
```

## Best Practices

### Do's ✅

- Write tests for all new functionality
- Use type hints
- Log informational messages
- Handle errors gracefully with fallbacks
- Keep functions small and focused
- Document non-obvious behavior
- Use fixtures for test setup
- Clean up after tests (tmp_path does this automatically)

### Don'ts ❌

- Don't write files outside tmp_path in tests
- Don't use bare `except:` clauses
- Don't modify global state in tests
- Don't hardcode paths (use Path constants)
- Don't skip error handling
- Don't test implementation details
- Don't commit commented-out code
- Don't ignore test failures

## Resources

- **pytest documentation**: <https://docs.pytest.org/>
- **Python pathlib**: <https://docs.python.org/3/library/pathlib.html>
- **Type hints**: <https://docs.python.org/3/library/typing.html>
- **PEP 8**: <https://pep8.org/>
