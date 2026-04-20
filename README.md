# ccBench - Code Creation Benchmarking Suite for Coding Agents

ccBench is a benchmarking suite designed to evaluate the performance of various coding agents on solving various tasks. It provides a standardized framework to test and compare how effectively different tooling variants can generate solutions to predefined tasks.

## Prerequisites

- `uv` - A virtual environment manager. Install it from [uv's GitHub repository](https://github.com/astral-sh/uv).
- `cloc` - A tool to count lines of code. Install it via your package manager (e.g., `brew install cloc` on macOS).

## Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/jannismain/ccBench.git
   cd ccBench
   ```

2. Fill in required secrets.

   Some configurations require API keys or other secrets to function properly.
   For example, if you are using the `portkey_for_claude_code` configuration, you need to set up the `.env` file with your Portkey API key.

    ```bash
    cd config_forge/portkey_for_claude_code
    cp .env.sample .env
    # Edit .env to add your Portkey API key
    ```

## Running Experiments

### Basic Usage

To run an experiment, use the following command:

```shell
uv run ccbench [experiment.yaml]
```

### Command-Line Options

- `experiment` - Experiment YAML file. You can pass a direct path, or just the file name relative to `experiments/`
- `--variant NAME` - Run only the specified variant from the experiment
- `--task NAME` - Run only the specified task from the experiment
- `--skip-run` - Prepare experiment directories and run setup scripts, but skip task execution
- `--results-dir PATH` - Write results under the given directory instead of using `$CCBENCH_RESULT` or `./results`

### Examples

```shell
# Run all variants in an experiment
uv run ccbench spec-driven-comparison.yaml

# Run baseline variant only
uv run ccbench spec-driven-comparison.yaml --variant baseline

# Run one task from the experiment
uv run ccbench spec-driven-comparison.yaml --task c4-stop-button

# Prepare directories and setup without executing run scripts
uv run ccbench spec-driven-comparison.yaml --skip-run

# Write results to a custom directory
uv run ccbench spec-driven-comparison.yaml --results-dir /tmp/ccbench-results

# Run an ad-hoc experiment without writing a YAML file first
uv run ccbench run --shard claude_code --shard cc_caveman --task aoc_2025_10

# Limit an ad-hoc experiment to specific evals
uv run ccbench run --shard claude_code --task aoc_2025_10 --eval cloc --eval claude_code_metrics

# Retry failed steps in an existing experiment result
uv run ccbench retry results/20260417_164519_simple

# Retry a manually selected step in one task result
uv run ccbench retry results/20260417_164519_simple/tasks/aoc_2025_01 --step 'run.*.claude_code.sh'
```

Ad-hoc experiments include every available eval shard by default. Passing one or
more `--eval` flags replaces that default list with the evals you specify.

Retry uses each task directory's `.ccbench-status.json` to find failed steps.
Use repeated `--step` flags to retry exact script names or globs manually.

## Available Evaluations

ccBench includes several evaluation tools to analyze the results of experiments:

### `cloc` - Code Line Counter

Counts the lines of code in files changed by the agent inside `project/`, based on git diff plus untracked files. This includes both newly created files and modifications to existing tracked files.

**Output:** `cloc.json` - Contains line counts by language and file type.

### `claude_code_metrics` - Claude Code Performance Metrics

Extracts detailed performance metrics from Claude Code's `output.json` file, including:

- **Duration metrics:** Total execution time (ms) and API time (ms)
- **Token usage:** Input tokens, cache creation/read tokens, output tokens
- **Cost:** Total cost in USD
- **Turns:** Number of conversation turns
- **Model usage:** Breakdown by model (Sonnet, Haiku, etc.) with individual costs
- **Permission denials:** Count of permission denials during execution
- **Web search requests:** Number of web searches performed

**Output:** `claude_code_metrics.json` - Contains all extracted metrics in JSON format.
