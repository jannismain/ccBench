import importlib.util
import json
from pathlib import Path

module_path = Path(__file__).with_name("cc_metrics.py")
spec = importlib.util.spec_from_file_location("cc_metrics", module_path)
assert spec is not None
cc_metrics = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cc_metrics)

aggregate_model_usage = cc_metrics.aggregate_model_usage
extract_metrics = cc_metrics.extract_metrics
extract_skill_usage = cc_metrics.extract_skill_usage


def test_aggregate_model_usage_sums_duplicate_models_once():
    metrics = [
        {"model_usage": {"claude-opus": 10, "claude-sonnet": 5}},
        {"model_usage": {"claude-opus": 3, "claude-haiku": 2}},
        {"model_usage": {"claude-sonnet": None, "claude-haiku": 4}},
    ]

    assert aggregate_model_usage(metrics) == {
        "claude-opus": 13,
        "claude-sonnet": 5,
        "claude-haiku": 6,
    }


def test_extract_metrics_with_multiple_results():
    fp_output = Path(__file__).with_name("sample_output.jsonl")
    with fp_output.open("r") as f:
        content = [json.loads(line) for line in f.readlines() if line.strip()]
    assert extract_metrics(content)


def test_extract_skill_usage_returns_counts(tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(
        json.dumps(
            {
                "skillUsage": {
                    "simplify": {"usageCount": 3, "lastUsedAt": 1776336502938},
                    "commit": {"usageCount": 1, "lastUsedAt": 1776336000000},
                }
            }
        )
    )
    result = extract_skill_usage(str(claude_json))
    assert result == {"simplify": 3, "commit": 1}


def test_extract_skill_usage_missing_file(tmp_path):
    result = extract_skill_usage(str(tmp_path / "nonexistent.json"))
    assert result == {}


def test_extract_skill_usage_malformed_json(tmp_path):
    bad_file = tmp_path / ".claude.json"
    bad_file.write_text("not json")
    result = extract_skill_usage(str(bad_file))
    assert result == {}


def test_extract_skill_usage_no_skill_usage_key(tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"migrationVersion": 11}))
    result = extract_skill_usage(str(claude_json))
    assert result == {}
