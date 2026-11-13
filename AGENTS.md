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
```

## Making Changes

### Adding New Features

1. **Write tests first** - TDD approach ensures testability
2. **Implement feature** - Keep it minimal and focused
3. **Update documentation** - README, USER_GUIDE, or AGENTS.md as appropriate
4. **Run test suite** - Ensure all tests pass
5. **Test manually** - Run actual experiments to verify

## Don'ts ❌

- Don't write files outside tmp_path in tests
- Don't use bare `except:` clauses
- Don't modify global state in tests
- Don't hardcode paths (use Path constants)
- Don't skip error handling
- Don't test implementation details
- Don't commit commented-out code
- Don't ignore test failures

