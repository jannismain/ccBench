"""Tests for the static_analysis eval shard."""

import json
import subprocess

import analyze as analyzer


def test_group_analyzable_files_includes_python_and_js_ts():
    """Static analysis should route Python and JS/TS files to their analyzers."""
    grouped = analyzer.group_analyzable_files(
        [
            "app.py",
            "src/App.tsx",
            "src/server.mjs",
            "README.md",
            "node_modules/pkg/index.js",
            "dist/generated.ts",
        ]
    )

    assert grouped == {
        "python": ["app.py"],
        "javascript": ["src/App.tsx", "src/server.mjs"],
    }


def test_get_changed_files_uses_ccbench_initial_state_commit(tmp_path):
    """Committed agent changes after the initial snapshot should still be analyzed."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    for command in [
        ["git", "init"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test User"],
        ["git", "config", "commit.gpgsign", "false"],
    ]:
        subprocess.run(command, cwd=project_dir, check=True, capture_output=True)

    (project_dir / "base.py").write_text("print('base')\n")
    subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "ccbench initial state"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )

    (project_dir / "agent.py").write_text("print('agent')\n")
    subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "agent changes"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )

    assert analyzer.get_changed_files(project_dir) == ["agent.py"]


def test_normalize_eslint_issues(tmp_path):
    """ESLint JSON is converted to the common static_analysis detail format."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    details = analyzer.normalize_eslint_issues(
        [
            {
                "filePath": str(project_dir / "src" / "app.ts"),
                "messages": [
                    {
                        "ruleId": "@typescript-eslint/no-unused-vars",
                        "severity": 2,
                        "message": "'x' is assigned a value but never used.",
                        "line": 4,
                    },
                    {
                        "ruleId": "no-console",
                        "severity": 1,
                        "message": "Unexpected console statement.",
                        "line": 5,
                    },
                ],
            }
        ],
        project_dir,
    )

    assert details == [
        {
            "tool": "eslint",
            "file": "src/app.ts",
            "code": "@typescript-eslint/no-unused-vars",
            "message": "'x' is assigned a value but never used.",
            "line": 4,
            "severity": "error",
        },
        {
            "tool": "eslint",
            "file": "src/app.ts",
            "code": "no-console",
            "message": "Unexpected console statement.",
            "line": 5,
            "severity": "warning",
        },
    ]


def test_main_aggregates_python_and_js_ts_results(tmp_path, monkeypatch):
    """Mixed projects should report combined ruff and ESLint results."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        analyzer,
        "get_changed_files",
        lambda _project_dir: ["app.py", "src/app.ts", "README.md"],
    )
    monkeypatch.setattr(
        analyzer,
        "run_ruff",
        lambda _files, _project_dir: [
            {
                "filename": "app.py",
                "code": "F401",
                "message": "Unused import",
                "location": {"row": 1},
            }
        ],
    )
    monkeypatch.setattr(
        analyzer,
        "run_eslint",
        lambda _files, _project_dir: [
            {
                "filePath": str(project_dir / "src" / "app.ts"),
                "messages": [
                    {
                        "ruleId": "no-console",
                        "severity": 1,
                        "message": "Unexpected console statement.",
                        "line": 2,
                    }
                ],
            }
        ],
    )

    analyzer.main()

    result = json.loads((tmp_path / "static_analysis.json").read_text())
    assert result["status"] == "completed"
    assert result["lint_errors"] == 1
    assert result["lint_warnings"] == 1
    assert result["files_analyzed"] == 2
    assert result["files_by_language"] == {"python": 1, "javascript": 1}
    assert [detail["tool"] for detail in result["details"]] == ["ruff", "eslint"]
