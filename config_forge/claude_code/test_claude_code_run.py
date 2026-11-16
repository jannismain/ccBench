"""Tests for the Claude Code config shard run script."""

import os
import subprocess
from pathlib import Path

RUN_SCRIPT = Path(__file__).with_name("run.sh")


def run_with_fake_claude(task_root: Path, tmp_path: Path) -> list[str]:
    """Run run.sh with a fake claude binary; return one line of args per prompt call."""
    (task_root / "project").mkdir(parents=True)
    calls = tmp_path / "calls"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *--print* ]]; then printf "%s\\n" "$*" >> "$CALLS_FILE"; fi\n'
        'echo "{\\"type\\": \\"result\\"}"\n'
    )
    fake_claude.chmod(0o755)
    result = subprocess.run(
        ["bash", str(RUN_SCRIPT)],
        cwd=task_root,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE": str(fake_claude), "CALLS_FILE": str(calls)},
    )
    assert result.returncode == 0, result.stderr
    return calls.read_text().splitlines()


class TestClaudeCodeRun:
    def test_single_prompt(self, tmp_path):
        """prompt.md is sent as one call without continuing a session."""
        task_root = tmp_path / "task"
        task_root.mkdir()
        (task_root / "prompt.md").write_text("only question")

        calls = run_with_fake_claude(task_root, tmp_path)

        assert len(calls) == 1
        assert calls[0].endswith("only question")
        assert "--continue" not in calls[0]
        assert (task_root / "output.json").read_text().count("result") == 1

    def test_prompts_dir_runs_turns_in_one_session(self, tmp_path):
        """prompts/*.md run in sorted order; later prompts continue the session."""
        task_root = tmp_path / "task"
        (task_root / "prompts").mkdir(parents=True)
        for name, text in [("2.md", "second"), ("1.md", "first"), ("3.md", "third")]:
            (task_root / "prompts" / name).write_text(text)

        calls = run_with_fake_claude(task_root, tmp_path)

        assert [c.rsplit(" ", 1)[-1] for c in calls] == ["first", "second", "third"]
        assert ["--continue" in c for c in calls] == [False, True, True]
        assert (task_root / "output.json").read_text().count("result") == 3
