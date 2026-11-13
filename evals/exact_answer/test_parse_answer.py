"""Tests for the exact_answer eval shard."""

import importlib.util
import json
from pathlib import Path

import pytest

module_path = Path(__file__).with_name("parse_answer.py")
spec = importlib.util.spec_from_file_location("exact_answer_parse", module_path)
assert spec is not None
parse_answer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(parse_answer)


class TestExactAnswerEval:
    expected = "41384454324123574919196129"

    @pytest.mark.parametrize(
        "answer_text",
        [
            "41384454324123574919196129",
            "41,384,454,324,123,574,919,196,129",
            "41 384 454 324 123 574 919 196 129",
            "41_384_454_324_123_574_919_196_129",
            "4.1384454324123574919196129e25",
            "41384454324123574919196128 + 1",
        ],
    )
    def test_check_answer_accepts_equivalent_representations(self, answer_text):
        """Equivalent numeric formats should pass the exact answer check."""
        matched, matched_value = parse_answer.check_answer(answer_text, self.expected)

        assert matched is True
        assert matched_value is not None

    def test_check_answer_rejects_wrong_value(self):
        """Wrong numeric answers should fail the exact answer check."""
        matched, matched_value = parse_answer.check_answer(
            "41384454324123574919196128",
            self.expected,
        )

        assert matched is False
        assert matched_value is None

    def test_collect_output_text_reads_claude_assistant_text(self, tmp_path):
        """Claude JSONL output should be reduced to assistant text blocks."""
        output = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "41,384,454,324,123,574,919,196,129",
                    }
                ]
            },
        }
        (tmp_path / "output.json").write_text(json.dumps(output) + "\n")

        text, sources = parse_answer.collect_output_text(tmp_path)

        assert sources == ["output.json"]
        assert "41,384,454,324,123,574,919,196,129" in text

    def test_collect_output_text_reads_response_text_first(self, tmp_path):
        """Plain llm shard output should be evaluated directly."""
        (tmp_path / "response.txt").write_text("41,384,454,324,123,574,919,196,129\n")
        (tmp_path / "output.json").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "wrong"}]},
                }
            )
            + "\n"
        )

        text, sources = parse_answer.collect_output_text(tmp_path)

        assert sources == ["response.txt", "output.json"]
        assert text.splitlines()[0] == "41,384,454,324,123,574,919,196,129"

    def test_collect_output_text_reads_numeric_response_as_plain_text(self, tmp_path):
        """A bare numeric response file should not be treated as JSON output."""
        (tmp_path / "response.txt").write_text("41384454324123574919196129\n")

        text, sources = parse_answer.collect_output_text(tmp_path)

        assert sources == ["response.txt"]
        assert text.strip() == "41384454324123574919196129"


class TestCompareMetrics:
    def test_main_writes_score(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXACT_ANSWER_EXPECTED", "42")
        output = {"type": "assistant", "message": {"content": [{"type": "text", "text": "42"}]}}
        (tmp_path / "output.json").write_text(json.dumps(output) + "\n")

        parse_answer.main()

        written = json.loads((tmp_path / "metrics" / "exact_answer.json").read_text())
        assert written["eval"] == "exact_answer"
        assert written["metrics"]["score"]["value"] == 1

    def test_missing_output_writes_empty_score(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        parse_answer.main()

        written = json.loads((tmp_path / "metrics" / "exact_answer.json").read_text())
        assert written["metrics"]["score"]["value"] is None
