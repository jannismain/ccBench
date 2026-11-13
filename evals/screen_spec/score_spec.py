#!/usr/bin/env python3
"""Score a screen spec written by an agent against the expected facts of the screen."""

import json
import re
import sys
from pathlib import Path

POSITIONS = {
    "left": "left",
    "center": "center",
    "centre": "center",
    "middle": "center",
    "right": "right",
}
MIN_FIELD_LABELS_FOR_SCREEN = 4


def norm(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def objects(spec: dict, key: str) -> list[dict]:
    items = spec.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def position(value) -> str:
    return POSITIONS.get(norm(value), norm(value))


def pane_hit(expected: dict, panes: list[dict]) -> bool:
    titles = {norm(t) for t in expected["any_of"]}
    return any(
        norm(p.get("title")) in titles and position(p.get("position")) == expected["position"]
        for p in panes
    )


def step_hit(index: int, expected: dict, steps: list[dict]) -> bool:
    if index >= len(steps):
        return False
    step = steps[index]
    return (
        norm(step.get("label")) == norm(expected["label"])
        and step.get("selected") is expected["selected"]
    )


def toggle_hit(expected: dict, toggles: list[dict]) -> bool:
    return any(
        norm(t.get("label")) == norm(expected["label"]) and t.get("on") is expected["on"]
        for t in toggles
    )


def field_hit(expected: dict, fields: list[dict]) -> bool:
    return any(
        norm(f.get("label")) == norm(expected["label"])
        and norm(f.get("value")) == norm(expected["value"])
        for f in fields
    )


def message_text(spec: dict) -> str:
    messages = spec.get("assistant_messages")
    if not isinstance(messages, list):
        return ""
    return norm(" ".join(str(m) for m in messages))


def score(spec: dict, expected: dict) -> dict:
    panes, steps = objects(spec, "panes"), objects(spec, "steps")
    toggles, fields = objects(spec, "toggles"), objects(spec, "fields")
    text = message_text(spec)

    layout_hits = (
        sum(pane_hit(p, panes) for p in expected["panes"])
        + sum(step_hit(i, s, steps) for i, s in enumerate(expected["steps"]))
        + sum(toggle_hit(t, toggles) for t in expected["toggles"])
    )
    layout_total = len(expected["panes"]) + len(expected["steps"]) + len(expected["toggles"])

    field_hits = sum(field_hit(f, fields) for f in expected["fields"])
    message_hits = sum(norm(m) in text for m in expected["messages_present"])
    content_total = len(expected["fields"]) + len(expected["messages_present"])

    field_labels = {norm(f.get("label")) for f in fields}
    labels_seen = sum(norm(f["label"]) in field_labels for f in expected["fields"])
    found = (
        message_hits > 0
        and not any(norm(m) in text for m in expected.get("messages_absent", []))
        and labels_seen >= MIN_FIELD_LABELS_FOR_SCREEN
    )
    return {
        "screen_found": int(found),
        "layout_score": layout_hits / layout_total,
        "content_score": (field_hits + message_hits) / content_total,
        "layout_points": [layout_hits, layout_total],
        "content_points": [field_hits + message_hits, content_total],
    }


def tool_uses(output: Path) -> list[dict]:
    if not output.exists():
        return []
    uses = []
    for line in output.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses.append(block)
    return uses


def tooling_calls(output: Path) -> dict:
    counts = {"mcp": 0, "skill": 0, "script": 0, "other": 0}
    for use in tool_uses(output):
        name, args = use.get("name", ""), use.get("input") or {}
        if name.startswith("mcp__"):
            counts["mcp"] += 1
        elif name == "Skill" and "excalidraw" in norm(args.get("skill", "")):
            counts["skill"] += 1
        elif name == "Bash" and "excalidraw.py" in str(args.get("command", "")):
            counts["script"] += 1
        else:
            counts["other"] += 1
    return counts


GRADING_FILES = ("expected.json", "score_spec")


def peeked(output: Path) -> bool:
    return any(
        name in json.dumps(use.get("input"))
        for use in tool_uses(output)
        for name in GRADING_FILES
    )


def load_spec(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {}, "screen-spec.json was not written"
    try:
        spec = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        return {}, f"screen-spec.json is not valid JSON: {error}"
    if not isinstance(spec, dict):
        return {}, "screen-spec.json is not a JSON object"
    return spec, None


COMPARE_METRICS_FILE = Path("metrics/screen_spec.json")


def write_compare_metrics(result: dict) -> None:
    """Contribute metrics to `ccbench compare`; None values render as missing."""
    calls = result["tooling_calls"]
    metrics = {
        "screen_found": {"value": result["screen_found"], "label": "Screen found", "format": "{:.0f}/1"},
        "layout": {"value": result["layout_score"], "label": "Spec layout", "format": "{:.0%}"},
        "content": {"value": result["content_score"], "label": "Spec content", "format": "{:.0%}"},
        "tooling_calls": {
            "value": calls["mcp"] + calls["skill"] + calls["script"],
            "label": "Skill/MCP/script calls",
            "format": "{:.0f}",
        },
    }
    COMPARE_METRICS_FILE.parent.mkdir(exist_ok=True)
    COMPARE_METRICS_FILE.write_text(json.dumps({"eval": "screen_spec", "metrics": metrics}, indent=2))


def main() -> None:
    expected = json.loads(Path("expected.json").read_text())
    spec, problem = load_spec(Path("project/screen-spec.json"))
    result = {
        "status": "completed",
        "spec_written": problem is None,
        "problem": problem,
        **score(spec, expected),
        "tooling_calls": tooling_calls(Path("output.json")),
        "peeked": peeked(Path("output.json")),
    }
    Path("screen_spec.json").write_text(json.dumps(result, indent=2))
    write_compare_metrics(result)
    print(
        f"screen_found={result['screen_found']} layout={result['layout_score']:.2f} "
        f"content={result['content_score']:.2f} tools={result['tooling_calls']}"
    )
    if problem:
        print(problem, file=sys.stderr)


if __name__ == "__main__":
    main()
