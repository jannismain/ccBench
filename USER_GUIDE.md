# ccBench User Guide

## Config Shard System

ccBench uses a modular configuration system called "config shards." Each shard is a directory in `config_forge/` that contains files to be copied into the experiment's project directory.

### How Config Shards Work

When setting up an experiment, ccBench:
1. Creates a project directory for each task
2. Copies files from base config shards (defined in `configs`)
3. Copies files from variant-specific config shards (if using variants)
4. Intelligently merges JSON and TOML files when conflicts occur

### File Merging

When config shards contain files with the same path, ccBench merges them instead of overwriting. This allows combining settings from multiple shards.

#### What Gets Merged

- **JSON files** (`.json`) - Configuration files, settings, package.json, etc.
- **TOML files** (`.toml`) - pyproject.toml, Cargo.toml, etc.
- **Directories** - Copied recursively with merging applied to files inside

#### What Gets Overwritten

- Text files (`.txt`, `.md`, etc.)
- Shell scripts (`.sh`)
- Any other file types

#### Merge Rules

The merging follows these rules:

1. **Nested dictionaries** - Merged recursively, preserving keys from both
2. **Lists/Arrays** - Extended (concatenated), not replaced
3. **Primitive values** - Overlay value overwrites base value
4. **New keys** - Added from overlay to result

#### Examples

##### Example 1: Simple Settings Merge

**Base config** (`claude_code/.claude/settings.json`):
```json
{
  "model": "sonnet",
  "maxTurns": 50
}
```

**Overlay config** (`tdd_guard_for_claude_code/.claude/settings.json`):
```json
{
  "maxTurns": 100,
  "timeout": 300
}
```

**Merged result**:
```json
{
  "model": "sonnet",
  "maxTurns": 100,
  "timeout": 300
}
```

##### Example 2: Nested Object Merge

**Base config**:
```json
{
  "hooks": {
    "pre-commit": ["lint"]
  },
  "mcpServers": {
    "filesystem": {
      "command": "fs-server"
    }
  }
}
```

**Overlay config**:
```json
{
  "hooks": {
    "pre-commit": ["tdd-guard"]
  },
  "mcpServers": {
    "tdd-guard": {
      "command": "tdd-guard-server"
    }
  }
}
```

**Merged result**:
```json
{
  "hooks": {
    "pre-commit": ["lint", "tdd-guard"]
  },
  "mcpServers": {
    "filesystem": {
      "command": "fs-server"
    },
    "tdd-guard": {
      "command": "tdd-guard-server"
    }
  }
}
```

Notice how:
- The `hooks.pre-commit` array was extended with both values
- The `mcpServers` object now contains both servers

##### Example 3: TOML Merge

**Base** (`pyproject.toml`):
```toml
[project]
name = "my-project"
dependencies = ["requests"]

[tool.pytest]
testpaths = ["tests"]
```

**Overlay** (`pyproject.toml`):
```toml
[project]
dependencies = ["pytest"]

[tool.ruff]
line-length = 100
```

**Merged result**:
```toml
[project]
name = "my-project"
dependencies = ["requests", "pytest"]

[tool.pytest]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

### Setup Scripts

Config shards can include a `setup.sh` script that runs automatically before the experiment starts.

#### How Setup Scripts Work

1. Place `setup.sh` in your config shard directory
2. ccBench automatically detects and runs it after git initialization
3. The script executes from within the project directory
4. Any exit code other than 0 triggers a warning (but doesn't stop the experiment)

#### Example Setup Script

**File**: `config_forge/tdd_guard_for_claude_code/setup.sh`
```bash
#!/bin/bash
# Install TDD Guard tooling

npm install -g tdd-guard
python -m venv .venv
source .venv/bin/activate
pip install tdd-guard-pytest
echo "Activate and use the \`.venv\` virtual environment for any Python development." > CLAUDE.md
```

#### Best Practices for Setup Scripts

- **Make them idempotent** - Safe to run multiple times
- **Check for existing installations** - Don't reinstall if already present
- **Use absolute paths or explicit cd commands** - Script runs from project dir
- **Exit with proper codes** - 0 for success, non-zero for failure
- **Document requirements** - Add comments explaining what tools are needed
- **Handle errors gracefully** - Don't let minor issues break the experiment

#### Example: Idempotent Setup Script

```bash
#!/bin/bash
set -e  # Exit on error

# Only install if not already present
if ! command -v tdd-guard &> /dev/null; then
    echo "Installing tdd-guard..."
    npm install -g tdd-guard
else
    echo "tdd-guard already installed"
fi

# Only create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

# Activate and install Python dependencies
source .venv/bin/activate
pip install --quiet tdd-guard-pytest

echo "Setup complete!"
```

## Creating Experiments

### Experiment File Structure

An experiment is defined in a YAML file in the `experiments/` directory:

```yaml
tasks:
  - aoc_2025_01

variants:
  baseline:
    []
  with_tdd_guard:
    - tdd_guard_for_claude_code

configs:
  - claude_code

evals:
  - cloc
  - claude_code_metrics
```

### Key Sections

- **tasks** - List of task directories from `tasks/` to run
- **variants** - Named configurations with additional config shards
  - Empty array `[]` means use only base configs
  - List of config shards to overlay on base configs
- **configs** - Base config shards applied to all variants
- **evals** - Evaluation scripts to run after task completion

### Running Specific Variants

Use the `--variant` flag to run only one variant:

```bash
uv run ccBench.py my_experiment.yaml --variant baseline
```

This is useful for:
- Testing a single configuration
- Iterating on specific variants
- Reducing experiment time during development

## Troubleshooting

### Merge Conflicts

If you see unexpected values in merged files, check:
1. The order of config shards (later shards override earlier ones)
2. Whether values should be arrays (for concatenation) or primitives (for replacement)
3. The merge logs - ccBench logs when it merges files

### Setup Script Failures

If a setup script fails:
1. Check the warning message for the exit code
2. Run the script manually in the project directory to debug
3. Verify all required tools are installed on your system
4. Check file permissions (`chmod +x setup.sh`)

### Variant Not Found

If you get "Variant not found" error:
1. Check the variant name spelling (case-sensitive)
2. Verify the experiment YAML has a `variants:` section
3. List available variants by running without `--variant` flag
