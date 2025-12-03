cd project
if [ -f .env ]; then
    source .env
else
    echo "No .env file found"
fi
claude --print --output-format stream-json --dangerously-skip-permissions --setting-sources local "$(cat ../prompt.md)" | tee ../output.json
