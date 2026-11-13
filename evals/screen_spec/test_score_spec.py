"""Tests for the screen_spec eval shard."""

import importlib.util
import json
from pathlib import Path

import pytest

module_path = Path(__file__).with_name("score_spec.py")
spec = importlib.util.spec_from_file_location("screen_spec_score", module_path)
assert spec is not None
score_spec = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(score_spec)

# Copy of tasks/excalidraw-screen/expected.json, so the eval tests stand alone.
EXPECTED_PATH = Path(__file__).parent / "fixtures" / "expected.json"


def perfect_spec(expected: dict) -> dict:
    positions = [p["position"] for p in expected["panes"]]
    return {
        "panes": [
            {"title": p["any_of"][0], "position": pos}
            for p, pos in zip(expected["panes"], positions, strict=True)
        ],
        "steps": [dict(s) for s in expected["steps"]],
        "toggles": [dict(t) for t in expected["toggles"]],
        "fields": [dict(f) for f in expected["fields"]],
        "assistant_messages": list(expected["messages_present"]),
    }


@pytest.fixture
def expected():
    return json.loads(EXPECTED_PATH.read_text())


class TestScoreSpec:
    def test_perfect_spec_scores_one(self, expected):
        """A spec restating every expected fact scores 1 on all axes."""
        result = score_spec.score(perfect_spec(expected), expected)

        assert result["screen_found"] == 1
        assert result["layout_score"] == 1.0
        assert result["content_score"] == 1.0

    def test_flipped_toggle_costs_one_layout_point(self, expected):
        """A wrong toggle state lowers layout only, not content or screen_found."""
        spec = perfect_spec(expected)
        spec["toggles"][0]["on"] = False

        result = score_spec.score(spec, expected)

        assert result["layout_score"] == pytest.approx(15 / 16)
        assert result["content_score"] == 1.0
        assert result["screen_found"] == 1

    def test_wrong_pane_position_costs_layout(self, expected):
        spec = perfect_spec(expected)
        spec["panes"][0]["position"] = "right"

        result = score_spec.score(spec, expected)

        assert result["layout_score"] == pytest.approx(15 / 16)

    def test_step_order_matters(self, expected):
        """Swapping two steps misplaces both of them."""
        spec = perfect_spec(expected)
        spec["steps"][1], spec["steps"][2] = spec["steps"][2], spec["steps"][1]

        result = score_spec.score(spec, expected)

        assert result["layout_score"] == pytest.approx(14 / 16)

    def test_selected_step_flag_is_scored(self, expected):
        spec = perfect_spec(expected)
        spec["steps"][0]["selected"] = False

        assert score_spec.score(spec, expected)["layout_score"] == pytest.approx(15 / 16)

    def test_missing_selected_flag_counts_wrong(self, expected):
        spec = perfect_spec(expected)
        del spec["steps"][3]["selected"]

        assert score_spec.score(spec, expected)["layout_score"] == pytest.approx(15 / 16)

    def test_right_pane_accepts_either_section_title(self, expected):
        spec = perfect_spec(expected)
        spec["panes"][2]["title"] = "procedure description"

        assert score_spec.score(spec, expected)["layout_score"] == 1.0

    def test_position_synonyms_are_normalised(self, expected):
        spec = perfect_spec(expected)
        spec["panes"][1]["position"] = "Middle"

        assert score_spec.score(spec, expected)["layout_score"] == 1.0

    def test_wrong_field_value_costs_content(self, expected):
        spec = perfect_spec(expected)
        spec["fields"][0]["value"] = "Client B"

        result = score_spec.score(spec, expected)

        assert result["content_score"] == pytest.approx(6 / 7)
        assert result["layout_score"] == 1.0

    def test_alternative_screen_message_means_wrong_screen(self, expected):
        """The tri-state alternative shows a German confirmation; quoting it is a miss."""
        spec = perfect_spec(expected)
        spec["assistant_messages"] = ["Dein Scoping wurde übernommen."]

        result = score_spec.score(spec, expected)

        assert result["screen_found"] == 0
        assert result["content_score"] == pytest.approx(5 / 7)

    def test_missing_messages_means_screen_not_found(self, expected):
        spec = perfect_spec(expected)
        spec["assistant_messages"] = []

        assert score_spec.score(spec, expected)["screen_found"] == 0

    def test_empty_spec_scores_zero(self, expected):
        result = score_spec.score({}, expected)

        assert result["screen_found"] == 0
        assert result["layout_score"] == 0.0
        assert result["content_score"] == 0.0

    def test_malformed_sections_do_not_raise(self, expected):
        """Agents may emit strings or nulls where lists of objects are expected."""
        spec = {
            "panes": "left",
            "steps": [None, "Scoping"],
            "toggles": {"a": 1},
            "fields": None,
        }

        result = score_spec.score(spec, expected)

        assert result["layout_score"] == 0.0


class TestToolingCalls:
    @staticmethod
    def write_output(path: Path, *blocks: dict) -> None:
        events = [{"type": "assistant", "message": {"content": [b]}} for b in blocks]
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    def test_counts_mcp_skill_and_script_calls(self, tmp_path):
        self.write_output(
            tmp_path / "output.json",
            {"type": "tool_use", "name": "mcp__excalidraw__import_scene", "input": {}},
            {"type": "tool_use", "name": "Skill", "input": {"skill": "excalidraw"}},
            {
                "type": "tool_use",
                "name": "Bash",
                "input": {
                    "command": "python3 -I /x/scripts/excalidraw.py list f.excalidraw.md"
                },
            },
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "f"}},
        )

        calls = score_spec.tooling_calls(tmp_path / "output.json")

        assert calls == {"mcp": 1, "skill": 1, "script": 1, "other": 2}

    def test_detects_peeking_at_grading_files(self, tmp_path):
        self.write_output(
            tmp_path / "output.json",
            {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "cat ../expected.json"},
            },
        )

        assert score_spec.peeked(tmp_path / "output.json") is True

    def test_detects_peeking_at_grader_script(self, tmp_path):
        self.write_output(
            tmp_path / "output.json",
            {"type": "tool_use", "name": "Read", "input": {"file_path": "../score_spec.py"}},
        )

        assert score_spec.peeked(tmp_path / "output.json") is True

    def test_ordinary_calls_are_not_peeking(self, tmp_path):
        self.write_output(
            tmp_path / "output.json",
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        )

        assert score_spec.peeked(tmp_path / "output.json") is False

    def test_missing_output_yields_zero_calls(self, tmp_path):
        assert score_spec.tooling_calls(tmp_path / "output.json") == {
            "mcp": 0,
            "skill": 0,
            "script": 0,
            "other": 0,
        }


class TestCompareMetrics:
    def test_sums_skill_mcp_and_script_calls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        score_spec.write_compare_metrics(
            {
                "screen_found": 1,
                "layout_score": 0.5,
                "content_score": 1.0,
                "tooling_calls": {"mcp": 1, "skill": 2, "script": 3, "other": 9},
            }
        )
        written = json.loads((tmp_path / "metrics" / "screen_spec.json").read_text())
        values = {key: metric["value"] for key, metric in written["metrics"].items()}
        assert values == {"screen_found": 1, "layout": 0.5, "content": 1.0, "tooling_calls": 6}
