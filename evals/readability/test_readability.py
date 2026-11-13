"""Tests for the readability eval shard."""

import importlib.util
import json
from pathlib import Path

module_path = Path(__file__).with_name("readability.py")
spec = importlib.util.spec_from_file_location("readability_eval", module_path)
assert spec is not None
readability = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(readability)


def write_jsonl(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def assistant(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class TestFinalAnswers:
    def test_prefers_result_events(self, tmp_path):
        """Each result event holds the final answer to one prompt."""
        path = write_jsonl(
            tmp_path / "output.json",
            [
                assistant("Thinking out loud."),
                {"type": "result", "result": "First."},
                {"type": "result", "result": "Second."},
            ],
        )
        assert readability.final_answers(path) == ["First.", "Second."]

    def test_falls_back_to_last_assistant_text(self, tmp_path):
        """Without a result event, the last assistant text is used."""
        path = tmp_path / "output.json"
        path.write_text(
            json.dumps(assistant("First."))
            + "\nnot json\n"
            + json.dumps(assistant("Last."))
            + "\n"
        )
        assert readability.final_answers(path) == ["Last."]


class TestComputeMetrics:
    def test_simple_prose(self):
        """Short words and sentences produce high reading ease."""
        metrics = readability.compute_metrics("The cat sat. The dog ran.")
        assert metrics["words"] == 6
        assert metrics["sentences"] == 2
        assert metrics["avg_words_per_sentence"] == 3
        assert metrics["flesch_reading_ease"] > 100

    def test_complex_prose_scores_harder(self):
        """Long polysyllabic sentences score lower ease and higher grade."""
        simple = readability.compute_metrics("Use a lock. It is fast.")
        complex_ = readability.compute_metrics(
            "Pessimistic concurrency methodologies systematically serialize "
            "conflicting transactional operations, guaranteeing consistency."
        )
        assert complex_["flesch_reading_ease"] < simple["flesch_reading_ease"]
        assert complex_["flesch_kincaid_grade"] > simple["flesch_kincaid_grade"]
        assert complex_["gunning_fog"] > simple["gunning_fog"]

    def test_markdown_structure_and_code_excluded(self):
        """Markdown elements are counted; code is excluded from prose metrics."""
        text = (
            "## Locking — overview\n\n"
            "- **Optimistic** wins\n"
            "- Pessimistic blocks\n\n"
            "```sql\nSELECT * FROM accounts FOR UPDATE;\n```\n"
        )
        metrics = readability.compute_metrics(text)
        assert metrics["headings"] == 1
        assert metrics["list_items"] == 2
        assert metrics["code_blocks"] == 1
        assert metrics["bold_spans"] == 1
        assert metrics["em_dashes"] == 1
        assert metrics["words"] == 6
        assert metrics["sentences"] == 3

    def test_empty_text(self):
        """Empty answers yield zero counts and no scores."""
        metrics = readability.compute_metrics("")
        assert metrics["words"] == 0
        assert metrics["sentences"] == 0
        assert metrics["flesch_reading_ease"] is None


class TestAggregate:
    def test_pools_answers_and_reports_stats(self):
        """Top-level counts cover all answers; stats summarize each answer."""
        result = readability.aggregate(["The cat sat. The dog ran.", "Use a lock."])
        assert result["answer_count"] == 2
        assert result["words"] == 9
        assert result["sentences"] == 3
        assert result["stats"]["words"] == {"mean": 4.5, "min": 3, "max": 6}
        assert [a["words"] for a in result["answers"]] == [6, 3]

    def test_stats_skip_empty_answers(self):
        """Answers without words do not distort readability score stats."""
        result = readability.aggregate(["", "Use a lock."])
        assert (
            result["stats"]["flesch_reading_ease"]["min"]
            == result["stats"]["flesch_reading_ease"]["max"]
        )
        assert result["stats"]["words"]["min"] == 0


class TestMain:
    def test_writes_metrics_file(self, tmp_path):
        output = write_jsonl(
            tmp_path / "output.json", [{"type": "result", "result": "Hi there."}]
        )
        target = tmp_path / "readability.json"
        assert readability.main(output, target) == 0
        data = json.loads(target.read_text())
        assert data["status"] == "completed"
        assert data["words"] == 2
        assert data["answer_count"] == 1

    def test_writes_compare_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output = write_jsonl(
            tmp_path / "output.json",
            [{"type": "result", "result": "Hi there."}, {"type": "result", "result": "Bye."}],
        )
        readability.main(output, tmp_path / "readability.json")
        written = json.loads((tmp_path / "metrics" / "readability.json").read_text())
        metrics = written["metrics"]
        assert written["eval"] == "readability"
        assert metrics["words"]["value"] == 3
        assert metrics["words"]["change"] == "percent"
        assert metrics["words_per_answer"]["value"] == 1.5
        assert metrics["answer_count"]["value"] == 2

    def test_missing_output_is_skipped(self, tmp_path):
        target = tmp_path / "readability.json"
        readability.main(tmp_path / "output.json", target)
        assert json.loads(target.read_text())["status"] == "skipped"
