# Decision Record: Configurable Project Directory Name

## Proposal

Make the `project` directory inside `experiment_task_root` configurable via a `PROJECT_DIR` environment variable, so tasks could provide a custom name for the agent workspace directory.

## Status

**Rejected**

## Context

The `project/` subdirectory serves two roles:

1. **Source routing convention** — inside each shard (`config_forge/*/project/`, `tasks/*/project/`), files under `project/` are copied to the agent workspace; files outside go to the task root.
2. **Destination directory** — `experiment_task_root/project/` is where the agent runs.

The proposal was to allow renaming only the destination (role 2), keeping `project/` as the source convention (role 1).

## Analysis

Renaming just the destination in ccBench.py is straightforward — resolve `PROJECT_DIR` before creating the output directory.

However, the `project` name is hardcoded across many scripts in config shards and tasks:

- `cd project` in run scripts (claude_code, bmad, opencode)
- `git clone ... project` in task staging scripts
- `--setting-sources project` in Claude CLI flags

Every existing script would need updating to use `"$PROJECT_DIR"`, and every future shard/task author would need to remember the envvar convention instead of the simpler hardcoded name.

## Decision

The maintenance cost across all shards and scripts outweighs the benefit of a customizable folder name. The hardcoded `project` convention is simple, consistent, and easy for new task authors to follow.
