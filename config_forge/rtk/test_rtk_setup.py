"""Tests for the RTK config shard setup script."""

import os
import subprocess
from pathlib import Path

import pytest

SETUP_SCRIPT = Path(__file__).with_name("setup.sh")


@pytest.mark.parametrize(
    ("agent_directory", "agent_name"),
    [(".pi", "pi"), (".claude", "claude")],
)
def test_setup_detects_agent_from_assembled_project(
    tmp_path: Path, agent_directory: str, agent_name: str
) -> None:
    """Setup selects the agent configured in the assembled project directory."""
    task_root = tmp_path / "task"
    project_dir = task_root / "project"
    project_dir.mkdir(parents=True)
    (project_dir / agent_directory).mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rtk_args = tmp_path / "rtk-args"
    (fake_bin / "rtk").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--version" ]]; then\n'
        '  echo "rtk test"\n'
        "else\n"
        '  printf "%s\\n" "$*" > "$RTK_ARGS_FILE"\n'
        "fi\n"
    )
    (fake_bin / "rtk").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        cwd=task_root,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RTK_ARGS_FILE": str(rtk_args),
        },
    )

    assert result.returncode == 0, result.stderr
    assert rtk_args.read_text().strip() == f"init --agent {agent_name}"
