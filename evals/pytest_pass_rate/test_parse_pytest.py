"""Tests for pytest log parsing (used by pytest_pass_rate eval)."""

import pytest


def test_strip_ansi():
    from parse_pytest import strip_ansi

    text = "\x1b[32m4 passed\x1b[0m in 0.14s"
    assert strip_ansi(text) == "4 passed in 0.14s"


def test_parse_all_passed():
    from parse_pytest import parse_pytest_summary

    log = "======== 4 passed in 0.14s ========"
    result = parse_pytest_summary(log)
    assert result is not None
    assert result["tests_passed"] == 4
    assert result["tests_failed"] == 0
    assert result["tests_run"] == 4
    assert result["pass_rate"] == 1.0
    assert result["duration_s"] == pytest.approx(0.14)


def test_parse_mixed_results():
    from parse_pytest import parse_pytest_summary

    log = "======== 2 passed, 1 failed in 0.5s ========"
    result = parse_pytest_summary(log)
    assert result is not None
    assert result["tests_passed"] == 2
    assert result["tests_failed"] == 1
    assert result["tests_run"] == 3
    assert result["duration_s"] == pytest.approx(0.5)


def test_parse_with_errors_and_skipped():
    from parse_pytest import parse_pytest_summary

    log = "======== 3 passed, 1 failed, 2 errors, 1 skipped in 1.2s ========"
    result = parse_pytest_summary(log)
    assert result is not None
    assert result["tests_passed"] == 3
    assert result["tests_failed"] == 3  # 1 failed + 2 errors
    assert result["tests_skipped"] == 1
    assert result["tests_run"] == 7


def test_parse_duration_with_minutes():
    from parse_pytest import parse_pytest_summary

    log = "======== 10 passed in 1m 3.45s ========"
    result = parse_pytest_summary(log)
    assert result is not None
    assert result["duration_s"] == pytest.approx(63.45)


def test_parse_no_pytest_output():
    from parse_pytest import parse_pytest_summary

    log = "just some random log output\nnothing to see here"
    assert parse_pytest_summary(log) is None


def test_parse_with_ansi_codes():
    from parse_pytest import parse_pytest_summary

    # Real pytest output has ANSI codes around the summary
    log = "\x1b[32m========== \x1b[32m\x1b[1m4 passed\x1b[0m\x1b[32m in 0.14s\x1b[0m\x1b[32m ===========\x1b[0m"
    result = parse_pytest_summary(log)
    assert result is not None
    assert result["tests_passed"] == 4


def test_extract_failures():
    from parse_pytest import extract_failures

    log = "FAILED test_solution.py::test_big_numbers - AssertionError\nFAILED test_solution.py::test_edge"
    failures = extract_failures(log)
    assert len(failures) == 2
    assert "test_solution.py::test_big_numbers" in failures
