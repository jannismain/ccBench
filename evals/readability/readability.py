#!/usr/bin/env python3
"""Compute readability metrics for the agent's answers in output.json.

Top-level metrics pool all answers; `stats` and `answers` give the per-answer view.
"""

import json
import re
import sys
from pathlib import Path

CODE_BLOCK_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MARKUP_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*|\|)|[*_|]", re.MULTILINE)
WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")
STAT_KEYS = [
    "words",
    "sentences",
    "flesch_reading_ease",
    "flesch_kincaid_grade",
    "gunning_fog",
    "avg_words_per_sentence",
    "list_items",
    "em_dashes",
]


def final_answers(path: Path) -> list[str]:
    """Return the result text of every prompt, falling back to the last assistant text message."""
    results = []
    last_assistant_text = ""
    for line in path.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result" and isinstance(obj.get("result"), str):
            results.append(obj["result"])
        elif obj.get("type") == "assistant":
            content = obj.get("message", {}).get("content", [])
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if any(texts):
                last_assistant_text = "\n".join(texts)
    return results or [last_assistant_text]


def count_syllables(word: str) -> int:
    word = word.lower()
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|[^laeiouy]ed|[^laeiouy]e)$", "", word)
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]+", word)))


def prose(text: str) -> str:
    """Strip code and markdown markup, keeping one sentence boundary per block line."""
    text = CODE_BLOCK_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" code ", text)
    text = LINK_RE.sub(r"\1", text)
    lines = []
    for line in text.splitlines():
        line = MARKUP_RE.sub("", line).strip()
        if line and not SENTENCE_END_RE.search(line[-1]):
            line += "."
        lines.append(line)
    return "\n".join(lines)


def compute_metrics(text: str) -> dict:
    plain = prose(text)
    words = WORD_RE.findall(plain)
    word_count = len(words)
    sentence_count = max(1, len(SENTENCE_END_RE.findall(plain))) if words else 0
    syllables = [count_syllables(w) for w in words]
    complex_words = sum(1 for s in syllables if s >= 3)

    metrics = {
        "characters": len(text),
        "words": word_count,
        "sentences": sentence_count,
        "headings": len(re.findall(r"^\s*#{1,6}\s", text, re.MULTILINE)),
        "list_items": len(re.findall(r"^\s*(?:[-*+]|\d+[.)])\s", text, re.MULTILINE)),
        "code_blocks": len(re.findall(r"^\s*```", text, re.MULTILINE)) // 2,
        "bold_spans": len(re.findall(r"\*\*[^*\n]+\*\*", text)),
        "em_dashes": text.count("—"),
        "avg_words_per_sentence": None,
        "avg_syllables_per_word": None,
        "flesch_reading_ease": None,
        "flesch_kincaid_grade": None,
        "gunning_fog": None,
    }
    if word_count == 0:
        return metrics

    words_per_sentence = word_count / sentence_count
    syllables_per_word = sum(syllables) / word_count
    metrics.update(
        {
            "avg_words_per_sentence": round(words_per_sentence, 2),
            "avg_syllables_per_word": round(syllables_per_word, 3),
            "flesch_reading_ease": round(
                206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word, 2
            ),
            "flesch_kincaid_grade": round(
                0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 2
            ),
            "gunning_fog": round(
                0.4 * (words_per_sentence + 100 * complex_words / word_count), 2
            ),
        }
    )
    return metrics


def aggregate(answers: list[str]) -> dict:
    """Pool all answers into one text for top-level metrics; add per-answer stats."""
    per_answer = [compute_metrics(text) for text in answers]
    stats = {}
    for key in STAT_KEYS:
        values = [m[key] for m in per_answer if m[key] is not None]
        if values:
            stats[key] = {
                "mean": round(sum(values) / len(values), 2),
                "min": min(values),
                "max": max(values),
            }
    return {
        **compute_metrics("\n\n".join(answers)),
        "answer_count": len(answers),
        "stats": stats,
        "answers": per_answer,
    }


def write_compare_metrics(result: dict, task_root: Path) -> None:
    """Contribute metrics to `ccbench compare`; None values render as missing."""
    words_per_answer = ((result.get("stats") or {}).get("words") or {}).get("mean")
    displayed = [
        ("answer_count", result.get("answer_count"), "Answers", "{:.1f}"),
        ("words", result.get("words"), "Answer words", "{:,.0f}"),
        ("words_per_answer", words_per_answer, "Words per answer", "{:.0f}"),
        ("flesch_reading_ease", result.get("flesch_reading_ease"), "Flesch reading ease", "{:.1f}"),
        ("flesch_kincaid_grade", result.get("flesch_kincaid_grade"), "Flesch-Kincaid grade", "{:.1f}"),
        ("gunning_fog", result.get("gunning_fog"), "Gunning fog", "{:.1f}"),
        ("avg_words_per_sentence", result.get("avg_words_per_sentence"), "Words per sentence", "{:.1f}"),
        ("list_items", result.get("list_items"), "Answer list items", "{:.1f}"),
        ("em_dashes", result.get("em_dashes"), "Answer em dashes", "{:.1f}"),
    ]
    metrics = {
        key: {"value": value, "label": label, "format": fmt}
        for key, value, label, fmt in displayed
    }
    metrics["words"]["change"] = "percent"
    compare_metrics_file = task_root / "metrics" / "readability.json"
    compare_metrics_file.parent.mkdir(exist_ok=True)
    compare_metrics_file.write_text(json.dumps({"eval": "readability", "metrics": metrics}, indent=2))


def main(output_file: Path, metrics_file: Path) -> int:
    if not output_file.exists():
        result = {"status": "skipped", "reason": f"{output_file} not found"}
    else:
        result = {"status": "completed", **aggregate(final_answers(output_file))}
    metrics_file.write_text(json.dumps(result, indent=2) + "\n")
    write_compare_metrics(result, metrics_file.parent)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output.json")
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("readability.json")
    sys.exit(main(output, target))
