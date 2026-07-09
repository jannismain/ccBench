#!/bin/bash
set -euo pipefail

export LLM=${LLM:-llm}
export LLM_USER_PATH="${LLM_USER_PATH:-$PWD/.llm}"

LMSTUDIO_BASE_URL="${LMSTUDIO_BASE_URL:-http://localhost:11444}"
LMSTUDIO_MODEL="${LMSTUDIO_MODEL:-google/gemma-4-31b-qat}"
LLM_MODEL_ID="${LLM_MODEL_ID:-lmstudio-gemma-4-31b-qat}"

case "$LMSTUDIO_BASE_URL" in
    */v1) OPENAI_API_BASE="$LMSTUDIO_BASE_URL" ;;
    */) OPENAI_API_BASE="${LMSTUDIO_BASE_URL}v1" ;;
    *) OPENAI_API_BASE="${LMSTUDIO_BASE_URL}/v1" ;;
esac

mkdir -p "$LLM_USER_PATH"
cat > "$LLM_USER_PATH/extra-openai-models.yaml" <<EOF
- model_id: ${LLM_MODEL_ID}
  model_name: ${LMSTUDIO_MODEL}
  api_base: ${OPENAI_API_BASE}
EOF

if [ -f prompt.md ]; then
    echo "Using prompt.md"
    PROMPT="$(cat prompt.md)"
elif [ -n "${AGENT_PROMPT:-}" ]; then
    echo "Using AGENT_PROMPT"
    PROMPT="$AGENT_PROMPT"
else
    echo "Using fallback prompt"
    PROMPT="What is 1 + 1?"
fi

"$LLM" prompt \
    --model "$LLM_MODEL_ID" \
    --no-log \
    --no-stream \
    "$PROMPT" | tee response.txt
