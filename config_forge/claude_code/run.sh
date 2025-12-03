cd project
source .env || echo "No .env file found"
claude --print --output-format stream-json --dangerously-skip-permissions --setting-sources local "$(cat ../prompt.md)" | tee ../output.json
