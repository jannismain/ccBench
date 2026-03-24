from pathlib import Path

from evals.llm_judge.llm_judge import describe_project_changes, extract_skills


def test_extract_skills_collects_diverse_shapes():
    events = [
        {"type": "result", "skills_used": ["alpha", "beta"]},
        {"type": "skill_use", "skill": "gamma"},
        {"type": "tool_use", "name": "code_edit"},
        {"skills": ["delta", "epsilon"]},
    ]

    assert extract_skills(events) == ["alpha", "beta", "code_edit", "delta", "epsilon", "gamma"]


def test_describe_project_changes_without_git(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('hi')\n", encoding="utf-8")

    context = describe_project_changes(project)

    assert "main.py" in context
    assert "print('hi')" in context
