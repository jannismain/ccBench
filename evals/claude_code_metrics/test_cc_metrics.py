import importlib.util
import json
from pathlib import Path

import pytest

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


def values(metrics: dict) -> dict:
    return {key: metric["value"] for key, metric in metrics.items()}


def test_compare_metrics_allocates_costs_per_model():
    overall = {
        "total_cost_usd": 0.057747,
        "duration_ms": 13360,
        "num_turns": 4,
        "usage": {"input_tokens": 6, "output_tokens": 555, "cache_read_input_tokens": 114530},
        "model_usage": {
            "model-a": {
                "inputTokens": 10,
                "cacheCreationInputTokens": 5,
                "cacheReadInputTokens": 15,
                "outputTokens": 30,
                "costUSD": 0.60,
            },
            "model-b": {
                "inputTokens": 20,
                "cacheCreationInputTokens": 0,
                "cacheReadInputTokens": 10,
                "outputTokens": 30,
                "costUSD": 0.60,
            },
        },
    }

    result = values(cc_metrics.compare_metrics(overall))

    assert result["cost_usd"] == 0.057747
    assert result["input_token_cost_usd"] == pytest.approx(0.30)
    assert result["cache_token_cost_usd"] == pytest.approx(0.30)
    assert result["output_token_cost_usd"] == pytest.approx(0.60)
    assert result["duration_s"] == 13.36
    assert result["turns"] == 4
    assert result["total_tokens"] == 120
    assert (result["input_tokens"], result["cache_tokens"], result["output_tokens"]) == (30, 30, 60)


def test_compare_metrics_allocates_usage_costs_without_model_usage():
    overall = {
        "total_cost_usd": 0.50,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 5,
            "output_tokens": 30,
        },
    }

    result = values(cc_metrics.compare_metrics(overall))

    assert result["total_tokens"] == 50
    assert result["input_token_cost_usd"] == pytest.approx(0.10)
    assert result["cache_token_cost_usd"] == pytest.approx(0.10)
    assert result["output_token_cost_usd"] == pytest.approx(0.30)
    assert result["duration_s"] is None


def test_main_writes_compare_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cc_metrics.main(str(Path(__file__).with_name("sample_output.jsonl")), "claude_code_metrics.json")

    written = json.loads((tmp_path / "metrics" / "claude_code_metrics.json").read_text())
    assert written["eval"] == "claude_code_metrics"
    assert written["metrics"]["cost_usd"]["value"] is not None
    assert written["metrics"]["cost_usd"]["change"] == "percent"
