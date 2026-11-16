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

The `cloc` eval uses that repository state to count only files changed in `project/`: tracked files from `git diff HEAD` plus untracked files. Generated build/cache directories and package-manager lockfiles such as `package-lock.json` are excluded.

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

**Overlay config** (`requesty_for_claude_code/project/.claude/settings.json`):

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
| `post.sh`    | After all `run.sh` scripts have completed                        | Stops post processing    |

#### How Script Execution Works

1. **Directory assembly is task-first**: the task is copied first, then config shards and eval shards are overlaid onto it
2. **Script ordering is still config-first**: script indices are assigned in config → task → eval order
3. **Scripts are renamed during copy**: `run.sh` → `run.{index:03d}.{shard_name}.sh` (zero-padded 3-digit index)
4. **Scripts execute in alphabetical order within each phase** (which preserves the script index order)
5. **Environment variables propagate** between all scripts - `staging.sh` → `setup.sh` → `run.sh` → `post.sh`
6. **Output is captured**: Each script's output is streamed to the terminal and saved to a `.log` file

Example of script ordering with 2 config shards, 1 task, and 2 eval shards:

```
setup.000.claude_code.sh     # config[0]
setup.001.requesty_for_claude_code.sh  # config[1]
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
# setup.001.requesty_for_claude_code.sh
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
claude --print --output-format stream-json --verbose "$(cat ../prompt.md)" | tee -a ../output.json
```

#### post.sh - Post-run Hook

Use `post.sh` when a shard needs cleanup or reporting after all `run.*.sh` scripts have completed. Like other script types, multiple `post.*.sh` scripts run in shard index order.

**Example**:

```bash
#!/bin/bash
set -e

echo "Run scripts finished at $(date)" >> post-summary.txt
```

If any `post.*.sh` script fails (non-zero exit), remaining post scripts are skipped.

#### Best Practices

- **Make scripts idempotent** - Safe to run multiple times
- **Use `export` for cross-script communication** - Environment propagates automatically
- **Check for existing state** - Don't reinstall if already present
- **Exit with proper codes** - 0 for success, non-zero for failure
- **Use `post.sh` for cleanup/reporting** - It runs after all `run.*.sh` scripts finish
- **Keep `staging.sh` simple** - Only modify your shard's own files

#### Example: Complete Shard with Multiple Scripts

```
config_forge/my_shard/
├── staging.sh          # Runs during staging (task root)
├── setup.sh            # Installs dependencies (task root)
├── run.sh              # Main execution (task root)
├── post.sh             # Post-run cleanup/reporting (task root)
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

### Contributing Metrics from Evals

`ccbench compare` knows nothing about individual evals. Each eval contributes the
metrics it wants compared by writing `metrics/<eval>.json` relative to the task
root (the working directory of eval scripts):

```json
{
  "eval": "readability",
  "metrics": {
    "words": {"value": 812, "label": "Answer words", "format": "{:,.0f}", "change": "percent"},
    "gunning_fog": {"value": 9.3, "label": "Gunning fog", "format": "{:.1f}"}
  }
}
```

- `value` is a number or `null`; `null` renders as `—`. Write `null` values when the
  eval is skipped or fails so a stale file from an earlier attempt is overwritten.
- `label` is the row name in the comparison table.
- `format` is a Python format string applied to the value, the mean, and the range
  (e.g. `"${:.4f}"`, `"{:.0%}"`, `"{:.0f}/5"`). Defaults to `"{}"`.
- `change: "percent"` adds the percent change against the baseline variant.

Metrics are keyed as `<eval>.<metric>` in `compare --json` output and in run
manifests, which also store the labels and formats so historical results render
even after an eval changes. Rows appear in the order of the experiment's `evals:`
list. Keep the detailed eval output in its own file (e.g. `readability.json`);
`metrics/` holds only what should be compared.

Results recorded before evals wrote `metrics/` are read by frozen legacy
extractors in `ccbench/legacy_metrics.py`. Do not extend that module for new evals.

## Creating Experiments

### Finding reusable historical baselines

New runs automatically write `.ccbench/manifest.json` inside each task result.
This records a permanent run ID, the ordered shard recipes (including unselected
variants), task inputs, staging outputs, prepared files, tool versions, tracked
environment settings, evaluator identities, execution status, and metrics.
Files are identified by SHA-256 content hashes and executable permissions.
Relevant input and final project files are retained in `.ccbench/objects/`,
deduplicated within each run, for later inspection without consulting today's shards.

Keep a baseline variant declared in your experiment, but run only the new variant:

```bash
uv run ccbench experiment.yaml --variant new_variant -n 5
```

For one resulting task directory, find historical candidates and their reasons:

```bash
uv run ccbench results find --for /path/to/tasks/demo_new_variant_run_1 \
  --history-dir /path/to/results --policy comparison.yaml
uv run ccbench results diff OLD_RUN_ID NEW_RUN_ID --history-dir /path/to/results
```

`find` and `diff` emit JSON. They accept a task-result path or permanent run ID;
`find` also accepts an experiment directory containing exactly one task result.
Discovery is read-only and scans portable manifests directly. Copies of the same
run are counted once. There is no database to migrate or rebuild.

To compare all newly generated repetitions against eligible historical baselines:

```bash
uv run ccbench compare /path/to/new-experiment-result --baseline-history \
  --baseline baseline --history-dir /path/to/results \
  --policy comparison.yaml --report /path/to/comparison-report.json
```

The history directory defaults to `CCBENCH_RESULT`, then `~/.ccbench/results`.
All qualifying samples are used. When several current repetitions are selected,
the historical baseline must qualify for every repetition in that task cohort.
Candidates must match the reference experiment's
declared baseline recipe, including shard contents, order, and overrides.
Task inputs and evaluator recipes must match. Differences contributed by the
declared variant shards are reported as intended experimental changes. Other
prepared-file changes are rejected unless explicitly permitted. Files changed
indirectly by setup may require a path rule because their ownership is unknown.

Example `comparison.yaml`:

```yaml
tools:
  claude_code:
    min: "2.1.262"
    max_exclusive: "2.2.0"
  specify:
    min: "4.4.6"
    max_exclusive: "5.0.0"
max_age_days: 90
```

These are example bounds, not claims about tool compatibility. Omit `specify`
when it is not part of the baseline. A tool rule requires that tool to be observed
in the candidate. Without a rule, shared tools require identical observed versions.
Rules can alternatively use `versions: ["2.1.262", "2.1.266"]`, which also supports
exact prerelease/build strings. Numeric bounds require `major.minor.patch`.
Both observed versions must satisfy the rule. If a baseline-only tool is absent
from the new variant, a tool rule supplies the required version constraint.

Additional optional policy fields:

```yaml
# Explicitly accept these generated file differences; globs match relative paths.
staged_paths:
  speckit: ["project/.specify/**"]
prepared_paths: ["project/.specify/**"]
# Changes under the separate starting runtime configuration snapshot.
runtime_paths: ["plugins/**"]
# Exact by default; relax only the fields relevant to your comparison.
ignore_fields: ["host.python", "harness.revision", "harness.source"]
# Default is [completed]. Nonzero scripts require explicit inclusion.
states: [completed, completed_with_errors]
```

A version exception never automatically waives a file difference. Unknown policy
keys and invalid ranges are errors. Reports preserve the full policy, run IDs,
manifests, metrics, and accepted/rejected differences. Report files are never
overwritten. If `--report` is omitted, the report is saved under
`<history-dir>/comparisons/`. `--json` emits the full report as well.

Ordinary `compare` separates runs with the same variant label but different
recorded identities using an `@fingerprint` suffix. Explicit historical comparison
can pool policy-compatible identities and labels the selected historical samples
with the requested baseline name.

### Tool metadata and collection limits

A config shard can declare bounded version probes and additional environment
variables in `metadata.yaml`:

```yaml
tools:
  my_agent:
    command: ["${MY_AGENT_BIN}", "--version"]
env: [MY_AGENT_MODEL, MY_AGENT_TEMPERATURE]
```

Commands are argument lists, with environment-variable substitution and a
five-second timeout. There is no implicit shell or package installation. The first
three-part version in successful output is recorded; unsuccessful probes record
an unknown version. Declarations are read from the captured shard snapshot.
Claude Code has a built-in adapter respecting `CLAUDE`/`CLAUDE_BIN`; the Claude
Code and SpecKit run scripts also record a version observation at run-script entry.
SpecKit records its staging version probe. Its moving Git source is **not pinned**
by this feature; generated snapshots provide additional evidence. Versions are
probed again after execution, and detected drift blocks historical matching.

Declared non-secret overrides and a built-in model/provider environment allowlist
are captured. Non-secret `.env` assignments are captured separately without the
raw file. Known secret values, sensitive filenames, and detected credential-bearing
files are excluded from file objects. `.git`, dependency/cache directories, logs,
and shard ignore rules are also excluded; exclusions are recorded. Symlinks are
recorded without following their targets. Local `.claude-runtime` configuration
gets a separate starting snapshot. Observed model identifiers are extracted from
Claude result events when available. External service state and arbitrary global
configuration cannot be reconstructed from this metadata.

Execution dates, local parallel-job count, OS release, and CPU count are recorded
for inspection; these descriptive fields do not automatically prevent pooling.
Inspect them when comparing duration or cost. Model, host platform/architecture,
Python version, harness code, and evaluator differences are strict by default.

Prepared-only, interrupted, setup-failed, unknown-schema, and legacy results do
not qualify for automatic historical matching. Existing results are never assigned
today's versions. You can still explicitly compare legacy result paths using the
ordinary `compare` command, without claiming verified compatibility.

Retrying a recorded run archives its manifest, logs, and JSON outputs under
`.ccbench/attempts/` and marks the mutable result `retried`, excluding it from
automatic reuse. A retry is not counted as a fresh independent sample. A valid
benchmark failure represented by a zero-exit evaluation and a failing score still
counts; infrastructure/nonzero-script outcomes are separately identified.

Automatic skipping of baseline execution and filling missing sample counts are
not implemented. `-n` continues to request new executions.

### Experiment File Structure

An experiment is defined in a YAML file in the `experiments/` directory:

```yaml
runs: 3  # Optional; defaults to 1, overridden by -n / --runs

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

### Multi-turn Tasks

With the `claude_code` config shard, a task can provide a `prompts/` directory instead of `prompt.md`. Each `prompts/*.md` file is sent in sorted order as the next turn of the same session (`--continue`), and all turns are appended to `output.json`. `claude_code_metrics` sums usage across turns, and `readability` reports pooled and per-answer metrics. See `tasks/three-questions/` for an example.

### Running Specific Variants

Use the `--variant` flag to run only one variant:

```bash
uv run ccbench my_experiment.yaml --variant baseline
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
