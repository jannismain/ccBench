#!/usr/bin/env python3
"""Check whether an agent output contains a configured exact numeric answer."""

import ast
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from operator import add, mul, sub, truediv
from pathlib import Path

DEFAULT_EXPECTED = "41384454324123574919196129"
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d[\d,_ ]*(?:\.\d[\d,_ ]*)?|\.\d[\d,_ ]+)"
    r"(?:[eE][-+]?\d+)?(?![\w.])"
)
EXPRESSION_RE = re.compile(r"[-+*/().\d,_ \t^]{5,}")


def normalize_decimal(text: str) -> Decimal | None:
    """Normalize common integer separators and scientific notation."""
    cleaned = re.sub(r"(?<=\d)[,_ ](?=\d)", "", text.strip())
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def values_match(candidate: Decimal, expected: Decimal) -> bool:
    return candidate == expected


def numeric_candidates(text: str) -> list[Decimal]:
    candidates = []
    for match in NUMBER_RE.finditer(text):
        value = normalize_decimal(match.group())
        if value is not None:
            candidates.append(value)
    return candidates


def expression_candidates(text: str) -> list[Decimal]:
    candidates = []
    for match in EXPRESSION_RE.finditer(text):
        expression = normalize_expression(match.group())
        if not expression:
            continue
        value = evaluate_expression(expression)
        if value is not None:
            candidates.append(value)
    return candidates


def normalize_expression(expression: str) -> str:
    expression = re.sub(r"(?<=\d)[,_ ](?=\d)", "", expression)
    expression = expression.replace("^", "**").strip()
    if not re.search(r"\d", expression):
        return ""
    if not re.search(r"[+\-*/]", expression):
        return ""
    return expression


def evaluate_expression(expression: str) -> Decimal | None:
    try:
        node = ast.parse(expression, mode="eval")
        value = eval_ast(node.body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, Decimal):
        return value
    return None


def eval_ast(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp):
        return eval_unary(node)
    if isinstance(node, ast.BinOp):
        return eval_binary(node)
    raise ValueError(f"unsupported expression node: {type(node).__name__}")


def eval_unary(node: ast.UnaryOp) -> Decimal:
    value = eval_ast(node.operand)
    if isinstance(node.op, ast.UAdd):
        return value
    if isinstance(node.op, ast.USub):
        return -value
    raise ValueError(f"unsupported unary operator: {type(node.op).__name__}")


def eval_binary(node: ast.BinOp) -> Decimal:
    left = eval_ast(node.left)
    right = eval_ast(node.right)
    operators = {
        ast.Add: add,
        ast.Sub: sub,
        ast.Mult: mul,
        ast.Div: truediv,
    }
    for operator_type, operation in operators.items():
        if isinstance(node.op, operator_type):
            return operation(left, right)
    if isinstance(node.op, ast.Pow):
        return eval_power(left, right)
    raise ValueError(f"unsupported binary operator: {type(node.op).__name__}")


def eval_power(left: Decimal, right: Decimal) -> Decimal:
    if right != right.to_integral_value():
        raise ValueError("non-integer exponent")
    exponent = int(right)
    if abs(exponent) > 1000:
        raise ValueError("exponent too large")
    return left**exponent


def extract_assistant_text_from_jsonl(path: Path) -> str:
    parts = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "assistant":
            parts.extend(extract_text_blocks(obj.get("message", {}).get("content", [])))
        elif isinstance(obj.get("content"), str):
            parts.append(obj["content"])
    return "\n".join(parts)


def extract_text_blocks(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return parts


def collect_output_text(root: Path) -> tuple[str, list[str]]:
    plain_text_files = [
        "response.txt",
    ]
    jsonl_output_files = [
        "output.json",
        "opencode_output.json",
        "judge_output.json",
    ]
    parts = []
    sources = []
    for filename in plain_text_files:
        path = root / filename
        if not path.exists():
            continue
        parts.append(path.read_text(errors="replace"))
        sources.append(filename)

    for filename in jsonl_output_files:
        path = root / filename
        if not path.exists():
            continue
        text = extract_assistant_text_from_jsonl(path)
        if not text.strip():
            text = path.read_text(errors="replace")
        parts.append(text)
        sources.append(filename)

    if parts:
        return "\n".join(parts), sources

    for path in sorted(root.glob("run.*.log")):
        parts.append(path.read_text(errors="replace"))
        sources.append(path.name)
    return "\n".join(parts), sources


def check_answer(text: str, expected_text: str) -> tuple[bool, str | None]:
    expected = normalize_decimal(expected_text)
    if expected is None:
        raise ValueError(f"invalid expected answer: {expected_text}")

    for candidate in numeric_candidates(text) + expression_candidates(text):
        if values_match(candidate, expected):
            return True, format(candidate, "f")
    return False, None


def main() -> None:
    expected = os.getenv("EXACT_ANSWER_EXPECTED", DEFAULT_EXPECTED)
    output_file = Path("exact_answer.json")
    text, sources = collect_output_text(Path("."))
    if not text.strip():
        result = {"status": "error", "reason": "no agent output found", "sources": sources}
        output_file.write_text(json.dumps(result, indent=2))
        print("Error: no agent output found", file=sys.stderr)
        return

    matched, matched_value = check_answer(text, expected)
    result = {
        "status": "completed",
        "expected": expected,
        "matched": matched,
        "score": 1 if matched else 0,
        "matched_value": matched_value,
        "sources": sources,
    }
    output_file.write_text(json.dumps(result, indent=2))
    print(f"Exact answer matched: {matched}")


if __name__ == "__main__":
    main()
