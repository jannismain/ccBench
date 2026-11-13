"""Frozen metric extraction for results recorded before evals wrote `metrics/*.json`.

Do not extend this module. New evals contribute their own compare metrics.
"""

import json
from collections.abc import Callable
from numbers import Real
from pathlib import Path

from .log import log

# Flat legacy key -> (namespaced key, label, format, change)
LEGACY_SPECS: dict[str, tuple[str, str, str, str | None]] = {
    "cost_usd": ("claude_code_metrics.cost_usd", "Cost (USD)", "${:.4f}", "percent"),
    "input_token_cost_usd": ("claude_code_metrics.input_token_cost_usd", "Input token cost (USD)", "${:.4f}", None),
    "cache_token_cost_usd": ("claude_code_metrics.cache_token_cost_usd", "Cache token cost (USD)", "${:.4f}", None),
    "output_token_cost_usd": ("claude_code_metrics.output_token_cost_usd", "Output token cost (USD)", "${:.4f}", None),
    "duration_s": ("claude_code_metrics.duration_s", "Duration (s)", "{:.1f}", "percent"),
    "turns": ("claude_code_metrics.turns", "Turns", "{:.0f}", None),
    "total_tokens": ("claude_code_metrics.total_tokens", "Total tokens", "{:,.0f}", "percent"),
    "input_tokens": ("claude_code_metrics.input_tokens", "Input tokens", "{:,.0f}", None),
    "cache_tokens": ("claude_code_metrics.cache_tokens", "Cache tokens", "{:,.0f}", None),
    "output_tokens": ("claude_code_metrics.output_tokens", "Output tokens", "{:,.0f}", "percent"),
    "loc_total": ("cloc.code", "Lines of code", "{:.0f}", "percent"),
    "loc_files": ("cloc.files", "Code files", "{:.0f}", None),
    "test_pass_rate": ("pytest_pass_rate.pass_rate", "Test pass rate", "{:.0%}", None),
    "tests_passed": ("pytest_pass_rate.tests_passed", "Tests passed", "{:.0f}", None),
    "tests_failed": ("pytest_pass_rate.tests_failed", "Tests failed", "{:.0f}", None),
    "test_duration_s": ("pytest_pass_rate.duration_s", "Test duration (s)", "{:.2f}", None),
    "lint_errors": ("static_analysis.lint_errors", "Lint errors", "{:.0f}", None),
    "lint_warnings": ("static_analysis.lint_warnings", "Lint warnings", "{:.0f}", None),
    "judge_readability": ("judge_session.readability", "Readability", "{:.0f}/5", None),
    "judge_idiomatic": ("judge_session.idiomatic_style", "Idiomatic style", "{:.0f}/5", None),
    "judge_error_handling": ("judge_session.error_handling", "Error handling", "{:.0f}/5", None),
    "judge_efficiency": ("judge_session.efficiency", "Efficiency", "{:.0f}/5", None),
    "exact_answer_score": ("exact_answer.score", "Exact answer", "{:.0f}/1", None),
    "spec_screen_found": ("screen_spec.screen_found", "Screen found", "{:.0f}/1", None),
    "spec_layout": ("screen_spec.layout", "Spec layout", "{:.0%}", None),
    "spec_content": ("screen_spec.content", "Spec content", "{:.0%}", None),
    "spec_tooling_calls": ("screen_spec.tooling_calls", "Skill/MCP/script calls", "{:.0f}", None),
    "proto_screen_found": ("prototype_check.screen_found", "Prototype: screen found", "{:.0f}/1", None),
    "proto_content": ("prototype_check.content", "Prototype: content", "{:.0%}", None),
    "proto_layout": ("prototype_check.layout", "Prototype: layout", "{:.0%}", None),
    "proto_states": ("prototype_check.states", "Prototype: states", "{:.0%}", None),
    "proto_interaction": ("prototype_check.interaction", "Prototype: interaction", "{:.0%}", None),
    "proto_tooling_calls": ("prototype_check.tooling_calls", "Prototype: skill/MCP/script calls", "{:.0f}", None),
    "answer_count": ("readability.answer_count", "Answers", "{:.1f}", None),
    "answer_words": ("readability.words", "Answer words", "{:,.0f}", "percent"),
    "words_per_answer": ("readability.words_per_answer", "Words per answer", "{:.0f}", None),
    "flesch_reading_ease": ("readability.flesch_reading_ease", "Flesch reading ease", "{:.1f}", None),
    "flesch_kincaid_grade": ("readability.flesch_kincaid_grade", "Flesch-Kincaid grade", "{:.1f}", None),
    "gunning_fog": ("readability.gunning_fog", "Gunning fog", "{:.1f}", None),
    "avg_words_per_sentence": ("readability.avg_words_per_sentence", "Words per sentence", "{:.1f}", None),
    "answer_list_items": ("readability.list_items", "Answer list items", "{:.1f}", None),
    "answer_em_dashes": ("readability.em_dashes", "Answer em dashes", "{:.1f}", None),
    "skill_used": ("skill_usage.used", "Skill used", "{:.0%}", None),
    "skill_invocations": ("skill_usage.invocations", "Skill invocations", "{:.1f}", None),
}


def upgrade_legacy_values(values: dict) -> tuple[dict, dict]:
    """Map flat legacy values to namespaced values and display specs."""
    upgraded, specs = {}, {}
    for legacy_key, (key, label, fmt, change) in LEGACY_SPECS.items():
        if legacy_key not in values:
            continue
        upgraded[key] = values[legacy_key]
        specs[key] = {"label": label, "format": fmt}
        if change:
            specs[key]["change"] = change
    return upgraded, specs


def read_legacy_metrics(task_dir: Path) -> tuple[dict, dict]:
    return upgrade_legacy_values(read_legacy_values(task_dir))


def extract_from_claude_metrics(data: dict) -> dict:
    overall = data.get("overall", {})
    usage = overall.get("usage", {})
    token_metrics = extract_token_metrics(overall)
    return {
        "cost_usd": overall.get("total_cost_usd"),
        "input_token_cost_usd": token_metrics.get("input_token_cost_usd"),
        "cache_token_cost_usd": token_metrics.get("cache_token_cost_usd"),
        "output_token_cost_usd": token_metrics.get("output_token_cost_usd"),
        "duration_s": (overall.get("duration_ms") or 0) / 1000,
        "turns": overall.get("num_turns"),
        "total_tokens": token_metrics.get("total_tokens"),
        "input_tokens": token_metrics.get("input_tokens", usage.get("input_tokens")),
        "cache_tokens": token_metrics.get("cache_tokens"),
        "output_tokens": token_metrics.get("output_tokens", usage.get("output_tokens")),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "is_error": overall.get("is_error"),
    }


def extract_token_metrics(overall: dict) -> dict:
    model_usage = overall.get("model_usage")
    if isinstance(model_usage, dict) and model_usage:
        return extract_model_usage_token_metrics(model_usage)

    usage = overall.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    return allocate_token_costs(
        input_tokens=usage.get("input_tokens"),
        cache_tokens=sum_numbers(
            usage.get("cache_creation_input_tokens"),
            usage.get("cache_read_input_tokens"),
        ),
        output_tokens=usage.get("output_tokens"),
        cost_usd=overall.get("total_cost_usd"),
    )


def extract_model_usage_token_metrics(model_usage: dict) -> dict:
    result = empty_token_metrics()
    for model in model_usage.values():
        if not isinstance(model, dict):
            continue
        metrics = allocate_token_costs(
            input_tokens=get_number(model, "inputTokens", "input_tokens"),
            cache_tokens=sum_numbers(
                get_number(
                    model,
                    "cacheCreationInputTokens",
                    "cache_creation_input_tokens",
                ),
                get_number(model, "cacheReadInputTokens", "cache_read_input_tokens"),
            ),
            output_tokens=get_number(model, "outputTokens", "output_tokens"),
            cost_usd=get_number(model, "costUSD", "cost_usd", "total_cost_usd"),
        )
        add_token_metrics(result, metrics)
    return result


def allocate_token_costs(
    *,
    input_tokens,
    cache_tokens,
    output_tokens,
    cost_usd,
) -> dict:
    result = {
        "input_tokens": normalize_number(input_tokens),
        "cache_tokens": normalize_number(cache_tokens),
        "output_tokens": normalize_number(output_tokens),
        "total_tokens": None,
        "input_token_cost_usd": None,
        "cache_token_cost_usd": None,
        "output_token_cost_usd": None,
    }
    numeric_token_values = [
        value
        for value in (
            result["input_tokens"],
            result["cache_tokens"],
            result["output_tokens"],
        )
        if is_number(value)
    ]
    total_tokens = sum(numeric_token_values) if numeric_token_values else None
    result["total_tokens"] = total_tokens
    if is_number(cost_usd) and total_tokens:
        result["input_token_cost_usd"] = (
            cost_usd * (result["input_tokens"] or 0) / total_tokens
        )
        result["cache_token_cost_usd"] = (
            cost_usd * (result["cache_tokens"] or 0) / total_tokens
        )
        result["output_token_cost_usd"] = (
            cost_usd * (result["output_tokens"] or 0) / total_tokens
        )
    return result


def empty_token_metrics() -> dict:
    return {
        "input_tokens": 0,
        "cache_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_token_cost_usd": None,
        "cache_token_cost_usd": None,
        "output_token_cost_usd": None,
    }


def add_token_metrics(total: dict, metrics: dict) -> None:
    for key, value in metrics.items():
        if is_number(value):
            if not is_number(total[key]):
                total[key] = 0
            total[key] += value


def get_number(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if is_number(value):
            return value
    return None


def sum_numbers(*values) -> float | int | None:
    numeric_values = [value for value in values if is_number(value)]
    if not numeric_values:
        return None
    return sum(numeric_values)


def normalize_number(value):
    return value if is_number(value) else None


def extract_from_cloc(data: dict) -> dict:
    summary = data.get("SUM", {})
    return {
        "loc_total": summary.get("code"),
        "loc_files": summary.get("nFiles"),
    }


def extract_from_test_pass_rate(data: dict) -> dict:
    if data.get("status") == "skipped":
        return {
            "test_pass_rate": None,
            "tests_passed": None,
            "tests_failed": None,
            "test_duration_s": None,
        }
    return {
        "test_pass_rate": data.get("pass_rate"),
        "tests_passed": data.get("tests_passed"),
        "tests_failed": data.get("tests_failed"),
        "test_duration_s": data.get("duration_s"),
    }


def extract_from_static_analysis(data: dict) -> dict:
    if data.get("status") == "skipped":
        return {"lint_errors": None, "lint_warnings": None}
    return {
        "lint_errors": data.get("lint_errors"),
        "lint_warnings": data.get("lint_warnings"),
    }


def extract_from_judge_scores(data: dict) -> dict:
    return {
        "judge_readability": data.get("readability"),
        "judge_idiomatic": data.get("idiomatic_style"),
        "judge_error_handling": data.get("error_handling"),
        "judge_efficiency": data.get("efficiency"),
    }


def extract_from_exact_answer(data: dict) -> dict:
    return {
        "exact_answer_score": data.get("score"),
        "exact_answer_matched": data.get("matched"),
    }


def extract_from_screen_spec(data: dict) -> dict:
    calls = data.get("tooling_calls") or {}
    return {
        "spec_screen_found": data.get("screen_found"),
        "spec_layout": data.get("layout_score"),
        "spec_content": data.get("content_score"),
        "spec_tooling_calls": sum(calls.get(k, 0) for k in ("mcp", "skill", "script")),
    }


def extract_from_prototype_check(data: dict) -> dict:
    def score(category: str):
        return (data.get(category) or {}).get("score")

    calls = data.get("tooling_calls") or {}
    return {
        "proto_screen_found": data.get("screen_found"),
        "proto_content": score("content"),
        "proto_layout": score("layout"),
        "proto_states": score("states"),
        "proto_interaction": score("interaction"),
        "proto_tooling_calls": sum(calls.get(k, 0) for k in ("mcp", "skill", "script")),
    }


def extract_from_readability(data: dict) -> dict:
    return {
        "answer_count": data.get("answer_count"),
        "answer_words": data.get("words"),
        "words_per_answer": ((data.get("stats") or {}).get("words") or {}).get("mean"),
        "flesch_reading_ease": data.get("flesch_reading_ease"),
        "flesch_kincaid_grade": data.get("flesch_kincaid_grade"),
        "gunning_fog": data.get("gunning_fog"),
        "avg_words_per_sentence": data.get("avg_words_per_sentence"),
        "answer_list_items": data.get("list_items"),
        "answer_em_dashes": data.get("em_dashes"),
    }


def extract_from_skill_usage(data: dict) -> dict:
    return {
        "skill_used": data.get("used"),
        "skill_invocations": data.get("invocations"),
    }


LEGACY_EVAL_FILES: dict[str, Callable[[dict], dict]] = {
    "claude_code_metrics.json": extract_from_claude_metrics,
    "cloc.json": extract_from_cloc,
    "test_pass_rate.json": extract_from_test_pass_rate,
    "static_analysis.json": extract_from_static_analysis,
    "judge_scores.json": extract_from_judge_scores,
    "exact_answer.json": extract_from_exact_answer,
    "screen_spec.json": extract_from_screen_spec,
    "prototype_check.json": extract_from_prototype_check,
    "readability.json": extract_from_readability,
    "skill_usage.json": extract_from_skill_usage,
}


def read_legacy_values(task_dir: Path) -> dict:
    """Read flat metric values from raw eval output files."""
    metrics = {}
    for filename, extractor in LEGACY_EVAL_FILES.items():
        filepath = task_dir / filename
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text())
                metrics.update(extractor(data))
            except (json.JSONDecodeError, KeyError) as e:
                log.warning(f"Failed to parse {filepath}: {e}")
    return metrics


def is_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)
