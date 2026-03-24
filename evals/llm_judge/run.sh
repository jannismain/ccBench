#!/bin/bash
set -euo pipefail

PROJECT_DIR="${JUDGE_PROJECT_DIR:-project}"
PROMPT_FILE="${JUDGE_PROMPT_FILE:-prompt.md}"
TRACE_FILE="${JUDGE_OUTPUT_JSON:-output.json}"
REVIEW_FILE="${JUDGE_REVIEW_PATH:-llm_judge_review.md}"
UV_BIN="${UV_BIN:-uv}"

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "uv is required to run the LLM judge evaluation."
    exit 1
fi

"$UV_BIN" run python llm_judge.py \
  --project "$PROJECT_DIR" \
  --prompt "$PROMPT_FILE" \
  --output-json "$TRACE_FILE" \
  --review "$REVIEW_FILE"
