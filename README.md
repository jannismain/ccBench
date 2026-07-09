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
   For example, if you are using the `requesty_for_claude_code` configuration, you need to set up the `.env` file with your Portkey API key.

    ```bash
    cd config_forge/requesty_for_claude_code
    cp .env.sample .env
    # Edit .env to add your Portkey API key
    ```

## Development

Run the local quality gate before handing off changes:

```shell
uv run python -m pytest -q
uv run ruff check .
uv run ty check
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
- `--results-dir PATH` - Write results under the given directory instead of using `$CCBENCH_RESULT` or `~/.ccbench/results`

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

# Results are written to ~/.ccbench/results by default

# Write results to a custom directory
uv run ccbench spec-driven-comparison.yaml --results-dir /tmp/ccbench-results

# Run an ad-hoc experiment without writing a YAML file first
uv run ccbench run --shard claude_code --shard cc_caveman --task aoc_2025_10

# Limit an ad-hoc experiment to specific evals
uv run ccbench run --shard claude_code --task aoc_2025_10 --eval cloc --eval claude_code_metrics

# Retry failed steps in an existing experiment result
uv run ccbench retry ~/.ccbench/results/20260417_164519_simple

# Retry a manually selected step in one task result
uv run ccbench retry ~/.ccbench/results/20260417_164519_simple/tasks/aoc_2025_01 --step 'run.*.claude_code.sh'
```

Ad-hoc experiments include every available eval shard by default. Passing one or
more `--eval` flags replaces that default list with the evals you specify.

Retry uses each task directory's `.ccbench-status.json` to find failed steps.
Use repeated `--step` flags to retry exact script names or globs manually.

Compare output reports one table per task, with variants as columns and sample
counts in each header, such as `baseline (n=3)`. If multiple results exist for
the same task and variant, numeric metrics are averaged before rendering and
the table reports the min/max range alongside the mean. Percentage changes are
reported for cost, duration, total token usage, output tokens, and lines of
code only between variants of the same task. A variant whose name contains
`baseline` is used as that task's reference; otherwise the first variant for the
task is used. Claude Code token metrics are reported as both aggregate token
usage and input/cache/output token usage, with cost allocated across those
categories from the reported model costs.

## Available Evaluations

ccBench includes several evaluation tools to analyze the results of experiments:

### `cloc` - Code Line Counter

Counts the lines of code in files changed by the agent inside `project/`, based on git diff plus untracked files. This includes both newly created files and modifications to existing tracked files.

**Output:** `cloc.json` - Contains line counts by language and file type.

### `static_analysis` - Lint Results

Runs static analysis on changed source files inside `project/`. Python files are checked with `ruff`; JavaScript and TypeScript files are checked with ESLint when an ESLint runner is available.

**Output:** `static_analysis.json` - Contains aggregate lint error and warning counts plus per-issue details.

### `claude_code_metrics` - Claude Code Performance Metrics

Extracts detailed performance metrics from Claude Code's `output.json` file, including:

- **Duration metrics:** Total execution time (ms) and API time (ms)
- **Token usage:** Total tokens plus input tokens, cache creation/read tokens, output tokens
- **Cost:** Total cost in USD, plus input/cache/output token cost breakout
- **Turns:** Number of conversation turns
- **Model usage:** Breakdown by model (Sonnet, Haiku, etc.) with individual costs
- **Permission denials:** Count of permission denials during execution
- **Web search requests:** Number of web searches performed

**Output:** `claude_code_metrics.json` - Contains all extracted metrics in JSON format.
