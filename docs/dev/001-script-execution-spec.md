# Script Execution Specification

## Overview

Config shards, task shards, and eval shards can define scripts (`staging.sh`, `setup.sh`, `run.sh`) that execute at different phases of the experiment lifecycle. When multiple shards define the same script type, all scripts run in order rather than overwriting each other.

## Script Types

| Script       | When it runs                                                     | Failure behavior         |
| ------------ | ---------------------------------------------------------------- | ------------------------ |
| `staging.sh` | Before shard is merged, operates on shard's own files in staging | Stops processing         |
| `setup.sh`   | After all shards merged, before experiment runs                  | Stops experiment         |
| `run.sh`     | Executes the main experiment task                                | Continues to next script |

## Shard Types, Assembly, and Script Order

There are two related orders to keep in mind:

### Filesystem Assembly

For task directory contents, ccBench assembles files like this:

1. **Task shard first** (`tasks/`) - Seed the task root and `project/` directory
2. **Config shards next** (`config_forge/`) - Overlay tooling and configuration onto the copied task
3. **Eval shards last** (`evals/`) - Overlay evaluation assets

After assembly, ccBench ensures the agent workspace at `project/` has its own git repository. If the task already supplied one inside `project/`, ccBench preserves it.

For tasks with `staging.sh`, the task shard is staged and copied during its normal task phase instead of using the fast task-first copy path.

### Script Order

Scripts still use shard-type order for indexing and execution:

1. **Config shards** (`config_forge/`) - Base configuration and tooling
2. **Task shards** (`tasks/`) - Task-specific files and scripts
3. **Eval shards** (`evals/`) - Evaluation scripts that analyze results

A global counter assigns consecutive indices to all scripts across shard types, ensuring correct execution order.

## Script Renaming

During copy, scripts are renamed with a zero-padded 3-digit index and shard name:

```
run.sh → run.{index:03d}.{shard_name}.sh
```

Example with 2 config shards, 1 task, and 2 evals:

```
setup.000.claude_code.sh     # config[0]
setup.001.tdd_guard.sh       # config[1]
setup.002.aoc_2025_01.sh     # task
setup.003.cloc.sh            # eval[0]
setup.004.metrics.sh         # eval[1]
```

Scripts execute in alphabetical order, which preserves the intended execution order.

## Environment Propagation

Environment variables propagate across all script executions:

- `staging.sh` → `setup.sh` → `run.sh`
- Scripts can `export` variables that subsequent scripts will see
- Environment flows forward through all scripts (one simple rule)

Example:

```bash
# setup.000.claude_code.sh
export VIRTUAL_ENV=/path/to/venv
```

```bash
# setup.001.tdd_guard.sh
# VIRTUAL_ENV is available here automatically
source $VIRTUAL_ENV/bin/activate
export CLOC_EXTRA_ARGS="--exclude-dir=.venv"
```

```bash
# run.003.cloc.sh
# CLOC_EXTRA_ARGS is available here
cd project
git diff --name-only HEAD
git ls-files --others --exclude-standard
cloc $CLOC_EXTRA_ARGS --list-file=<changed-files>
```

## Staging for staging.sh

When a shard has a `staging.sh` script:

1. Copy shard files to a temporary staging directory
2. Run `staging.sh` in staging (shard sees its original `run.sh`, not renamed)
3. Rename scripts with index prefix
4. Overlay staged files into the assembled experiment task directory

This allows `staging.sh` to modify its own `run.sh` before merging.

## Output Handling

Script output is:

1. **Streamed** to the user in real-time
2. **Captured** to a log file with the same name as the script (`.log` extension)

Example: `run.001.claude_code.sh` → `run.001.claude_code.log`

Both stdout and stderr are captured to the same log file.

## Failure Behavior

| Script type  | On failure                  |
| ------------ | --------------------------- |
| `staging.sh` | Stop processing this shard  |
| `setup.sh`   | Stop experiment setup       |
| `run.sh`     | Continue to next run script |

## Backward Compatibility

Existing shards with `setup.sh` or `run.sh` will work without changes. Scripts are automatically renamed during copy.
