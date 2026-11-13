"""Tests for the judge_session eval."""

import importlib.util
import json
from pathlib import Path

module_path = Path(__file__).with_name("parse_judge.py")
spec = importlib.util.spec_from_file_location("judge_session_parse", module_path)
assert spec is not None
parse_judge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(parse_judge)


def judge_event(text: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


class TestMain:
    def test_writes_scores_and_compare_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        scores = {"readability": 4, "idiomatic_style": 3, "error_handling": 2, "efficiency": 5}
        (tmp_path / "judge_output.json").write_text(
            judge_event(f"```json\n{json.dumps(scores)}\n```") + "\n"
        )

        parse_judge.main()

        assert json.loads((tmp_path / "judge_scores.json").read_text()) == scores
        written = json.loads((tmp_path / "metrics" / "judge_session.json").read_text())
        assert {key: metric["value"] for key, metric in written["metrics"].items()} == scores

    def test_unparseable_judge_output_writes_empty_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "judge_output.json").write_text(judge_event("no scores") + "\n")

        parse_judge.main()

        assert json.loads((tmp_path / "judge_scores.json").read_text())["status"] == "error"
        written = json.loads((tmp_path / "metrics" / "judge_session.json").read_text())
        assert all(metric["value"] is None for metric in written["metrics"].values())
