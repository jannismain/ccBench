"""Tests for the skill_usage eval shard."""

import importlib.util
import json
from pathlib import Path

module_path = Path(__file__).with_name("skill_usage.py")
spec = importlib.util.spec_from_file_location("skill_usage_eval", module_path)
assert spec is not None
skill_usage = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(skill_usage)


def tool_use(name: str, **args) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
    }


def run(tmp_path: Path, events: list[dict], skill: str = "unslop") -> dict:
    output = tmp_path / "output.json"
    output.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    target = tmp_path / "skill_usage.json"
    skill_usage.main(output, target, skill)
    return json.loads(target.read_text())


class TestSkillUsage:
    def test_skill_tool_invocation(self, tmp_path):
        """Skill tool calls count, including plugin-namespaced names."""
        result = run(
            tmp_path,
            [tool_use("Skill", skill="unslop"), tool_use("Skill", skill="pstack:unslop")],
        )
        assert result["invocations"] == 2
        assert result["used"] == 1

    def test_reading_skill_md_counts(self, tmp_path):
        """Reading the SKILL.md directly is also usage."""
        result = run(tmp_path, [tool_use("Read", file_path="/p/.claude/skills/unslop/SKILL.md")])
        assert result["skill_md_reads"] == 1
        assert result["used"] == 1

    def test_other_skills_ignored(self, tmp_path):
        result = run(
            tmp_path,
            [tool_use("Skill", skill="simplify"), tool_use("Read", file_path="README.md")],
        )
        assert result["invocations"] == 0
        assert result["skill_md_reads"] == 0
        assert result["used"] == 0

    def test_missing_output_is_skipped(self, tmp_path):
        target = tmp_path / "skill_usage.json"
        skill_usage.main(tmp_path / "output.json", target, "unslop")
        assert json.loads(target.read_text())["status"] == "skipped"

    def test_writes_compare_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(tmp_path, [tool_use("Skill", skill="unslop")])
        written = json.loads((tmp_path / "metrics" / "skill_usage.json").read_text())
        assert written["metrics"]["used"]["value"] == 1
        assert written["metrics"]["invocations"]["value"] == 1
