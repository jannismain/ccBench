"""Tests for the cloc eval shard."""

import json
import subprocess
from pathlib import Path

from ccbench.scripts import ensure_project_git_repo

RUN_SCRIPT = Path(__file__).with_name("run.sh")


def test_cloc_eval_uses_git_diff_changed_files(tmp_path):
    """cloc eval counts changed source files while ignoring generated metadata."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "existing.py").write_text("print('base')\n")
    (project_dir / "untouched.py").write_text("print('untouched')\n")
    ensure_project_git_repo(project_dir)

    (project_dir / "existing.py").write_text("print('base')\nprint('changed')\n")
    (project_dir / "new.js").write_text("console.log('new');\n")
    (project_dir / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    (project_dir / "dist").mkdir()
    (project_dir / "dist" / "generated.js").write_text("console.log('generated');\n")

    result = subprocess.run(
        ["bash", str(RUN_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    cloc_output = json.loads((tmp_path / "cloc.json").read_text())
    assert cloc_output["header"]["n_files"] == 2
    assert cloc_output["SUM"]["nFiles"] == 2
    assert cloc_output["Python"]["nFiles"] == 1
    assert cloc_output["JavaScript"]["nFiles"] == 1
    compare_metrics = json.loads((tmp_path / "metrics" / "cloc.json").read_text())
    assert compare_metrics["eval"] == "cloc"
    assert compare_metrics["metrics"]["code"]["value"] == cloc_output["SUM"]["code"]
    assert compare_metrics["metrics"]["files"]["value"] == 2
