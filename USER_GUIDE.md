# ccBench User Guide

## Config Shard System

ccBench uses a modular configuration system called "config shards." Each shard is a directory in `config_forge/` that contains files to be overlaid onto the experiment's task directory.

### How Task Assembly Works

When setting up an experiment, ccBench:

1. Creates a task directory for each task with a `project/` subdirectory (the agent workspace)
2. Copies the task files first to seed the task directory
3. Overlays files from base config shards (defined in `configs`)
4. Overlays files from variant-specific config shards (if using variants)
5. Applies eval shards
6. Intelligently merges JSON and TOML files when overlay conflicts occur

For large tasks without `staging.sh`, the initial task copy uses a fast no-merge path. If a task defines `staging.sh`, it is staged and copied during its normal task phase instead.

### File Routing

Files in a shard are routed to two destinations:

- **`project/` subdirectory** in the shard → copied into `project/` in the task directory (agent workspace)
- **Everything else** at the shard root → copied into the task root directory (ccBench infrastructure)

This keeps the agent workspace clean — only files the agent needs are in `project/`, while run scripts, eval scripts, and metrics stay at the task root.

ccBench also ensures `project/` is the git repository the agent sees. If the task already provides a repo in `project/` (for example via `git clone` during staging), ccBench leaves that repo intact. Otherwise it creates an initial commit inside `project/`.

The `cloc` eval uses that repository state to count only files changed in `project/`: tracked files from `git diff HEAD` plus untracked files.

### File Merging

The task is copied first without merge logic. After that, config and eval shards are applied as overlays. When an overlay shard contains a JSON or TOML file that already exists in the assembled task directory, ccBench merges it instead of overwriting it. This allows combining settings from multiple shards while keeping large task copies fast.

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

**Overlay config** (`portkey_for_claude_code/project/.claude/settings.json`):

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
    "pre-commit": ["portkey"]
  },
  "mcpServers": {
    "portkey": {
      "command": "portkey-server"
    }
  }
}
```

**Merged result**:

```json
{
  "hooks": {
    "pre-commit": ["lint", "portkey"]
  },
  "mcpServers": {
    "filesystem": {
      "command": "fs-server"
    },
    "portkey": {
      "command": "portkey-server"
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

### Scripts

Config shards, task shards, and eval shards can include scripts that run at different phases of the experiment lifecycle. When multiple shards define the same script type, all scripts run in order based on shard type (config → task → eval).

#### Script Types

| Script       | When it runs                                                     | Failure behavior         |
| ------------ | ---------------------------------------------------------------- | ------------------------ |
| `staging.sh` | Before shard is merged, operates on shard's own files in staging | Stops processing         |
| `setup.sh`   | After all shards merged, before experiment runs                  | Stops experiment         |
| `run.sh`     | Executes the main experiment task                                | Continues to next script |

#### How Script Execution Works

1. **Directory assembly is task-first**: the task is copied first, then config shards and eval shards are overlaid onto it
2. **Script ordering is still config-first**: script indices are assigned in config → task → eval order
3. **Scripts are renamed during copy**: `run.sh` → `run.{index:03d}.{shard_name}.sh` (zero-padded 3-digit index)
4. **Scripts execute in alphabetical order** (which preserves the script index order)
5. **Environment variables propagate** between all scripts - `staging.sh` → `setup.sh` → `run.sh`
6. **Output is captured**: Each script's output is streamed to the terminal and saved to a `.log` file

Example of script ordering with 2 config shards, 1 task, and 2 eval shards:

```
setup.000.claude_code.sh     # config[0]
setup.001.portkey_for_claude_code.sh  # config[1]
setup.002.aoc_2025_01.sh     # task
setup.003.cloc.sh            # eval[0]
setup.004.metrics.sh         # eval[1]
```

#### Environment Propagation

Environment variables flow forward through all script phases. Once a variable is exported, all subsequent scripts see it:

```bash
# setup.000.claude_code.sh
export VIRTUAL_ENV=/path/to/venv
source $VIRTUAL_ENV/bin/activate
```

```bash
# setup.001.portkey_for_claude_code.sh
# VIRTUAL_ENV is available here automatically
pip install requests
export CLOC_EXTRA_ARGS="--exclude-dir=.venv"
```

```bash
# run.003.cloc.sh  (eval shard)
# Both VIRTUAL_ENV and CLOC_EXTRA_ARGS are available
cd project
git diff --name-only HEAD
git ls-files --others --exclude-standard
cloc $CLOC_EXTRA_ARGS --list-file=<changed-files>
```

#### staging.sh - Pre-merge Script

Use `staging.sh` when your shard needs to modify its own files before merging. The script runs in isolation and sees the shard's original filenames (e.g., `run.sh`, not the renamed version).

**Example**: Dynamically generating a run script:

```bash
#!/bin/bash
# staging.sh - runs before shard is merged

# Append additional commands to our run.sh
cat >> run.sh << 'EOF'
echo "Additional logging from my_shard"
EOF
```

#### setup.sh - Post-merge Setup

Use `setup.sh` for initialization that needs the complete merged environment. This is where you install dependencies, create virtual environments, etc.

**Example**:

```bash
#!/bin/bash
set -e

# Create virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Export for subsequent scripts
export VIRTUAL_ENV="$(pwd)/.venv"
```

If any `setup.*.sh` script fails (non-zero exit), the experiment stops.

#### run.sh - Main Execution

Use `run.sh` for the actual experiment execution. Multiple `run.*.sh` scripts execute in order, and failures don't stop subsequent scripts.

**Example**:

```bash
#!/bin/bash
cd project
claude --print --output-format stream-json --verbose "$(cat ../prompt.md)" | tee ../output.json
```

#### Best Practices

- **Make scripts idempotent** - Safe to run multiple times
- **Use `export` for cross-script communication** - Environment propagates automatically
- **Check for existing state** - Don't reinstall if already present
- **Exit with proper codes** - 0 for success, non-zero for failure
- **Keep `staging.sh` simple** - Only modify your shard's own files

#### Example: Complete Shard with Multiple Scripts

```
config_forge/my_shard/
├── staging.sh          # Runs during staging (task root)
├── setup.sh            # Installs dependencies (task root)
├── run.sh              # Main execution (task root)
└── project/            # Agent workspace files
    └── .claude/
        └── settings.json   # Merged with other shards
```

**staging.sh**:

```bash
#!/bin/bash
# Add timestamping to our run script
sed -i '1a echo "Started at $(date)"' run.sh
```

**setup.sh**:

```bash
#!/bin/bash
set -e
npm install -g my-tool
export MY_TOOL_INSTALLED=true
```

**run.sh**:

```bash
#!/bin/bash
my-tool analyze .
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
  with_portkey:
    - portkey_for_claude_code

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
- **configs** - Base config shards overlaid onto the copied task for all variants
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

1. The order of config shards (later config shards override earlier ones, and all config overlays land after the initial task copy)
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
