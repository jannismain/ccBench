# ccBench - Coding Characteristics Benchmarking Suite for Agentic Coding Tools

ccBench is a benchmarking suite designed to evaluate the performance of various agentic coding tools and configurations on solving various tasks. It provides a standardized framework to test and compare how effectively different tooling variants can generate solutions to predefined tasks.

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
   For example, if you are using the `claude_code_models_via_portkey` configuration, you need to set up the `.env` file with your Portkey API key.

    ```bash
    cd config_forge/claude_code_models_via_portkey
    cp .env.sample .env
    # Edit .env to add your Portkey API key
    ```

## Running Experiments

To run an experiment, use the following command:

```bash
uv run ccBench.py example.yaml
```
