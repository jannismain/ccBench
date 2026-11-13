#!/usr/bin/env python3
"""Open an agent-built HTML prototype in headless Chrome and score it against expected facts.

The checks locate labels by visible text and measure geometry, so they do not depend on the
markup the agent chose. Switch state is read from native checkboxes and ARIA attributes.
"""

import glob
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VIEWPORT = {"width": 1280, "height": 800}
ROW_TOLERANCE_PX = 20
COLUMN_TOLERANCE_PX = 12
MIN_STEPS_FOR_COLUMN = 6
MIN_FIELD_LABELS_FOR_SCREEN = 4
MIN_SWITCHES_FOR_SCREEN = 4

COLLECT_JS = """
() => {
  const norm = (s) => s.replace(/\\s+/g, ' ').trim().toLowerCase();
  const visible = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    }
    return true;
  };
  const effectiveBg = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const bg = getComputedStyle(e).backgroundColor;
      const m = bg.match(/rgba?\\(([^)]+)\\)/);
      if (m) {
        const parts = m[1].split(',').map((p) => parseFloat(p));
        if (parts.length < 4 || parts[3] > 0) return bg;
      }
    }
    return 'none';
  };
  const sig = (el) => {
    const cs = getComputedStyle(el);
    return [effectiveBg(el), cs.color, cs.fontWeight].join('|');
  };
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = norm(n.textContent);
    if (!t || !n.parentElement || !visible(n.parentElement)) continue;
    const tag = n.parentElement.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TITLE') continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    const r = range.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    texts.push({t, kind: 'text', x: r.x, y: r.y, w: r.width, h: r.height, sig: sig(n.parentElement)});
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const type = (el.type || '').toLowerCase();
    if (['checkbox', 'radio', 'button', 'submit'].includes(type)) continue;
    const value = el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).textContent || '' : el.value;
    if (norm(value)) texts.push({t: norm(value), kind: 'value', x: r.x, y: r.y, w: r.width, h: r.height, sig: sig(el)});
    if (norm(el.placeholder || '')) texts.push({t: norm(el.placeholder), kind: 'placeholder', x: r.x, y: r.y, w: r.width, h: r.height, sig: sig(el)});
  }
  const controls = [];
  const selector = 'input[type=checkbox], input[type=radio], [role=switch], [role=checkbox], button[aria-pressed], [aria-checked]';
  document.querySelectorAll(selector).forEach((el, index) => {
    el.setAttribute('data-bench-control', String(index));
    const isInput = el.tagName === 'INPUT';
    const state = isInput ? el.checked
      : (el.getAttribute('aria-checked') || el.getAttribute('aria-pressed')) === 'true';
    let box = el.getBoundingClientRect();
    const labelEl = (el.labels && el.labels[0]) || el.closest('label');
    if (box.width === 0 || box.height === 0) box = (labelEl || el.parentElement).getBoundingClientRect();
    let owner = '';
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const t = norm(e.innerText || '');
      if (t) { owner = t; break; }
    }
    controls.push({index, state, owner, label: labelEl ? norm(labelEl.innerText || '') : '',
                   x: box.x, y: box.y, w: box.width, h: box.height, native: isInput});
  });
  return {texts, controls, body: norm(document.body.innerText || ''),
          scrollWidth: document.documentElement.scrollWidth,
          scrollHeight: document.documentElement.scrollHeight};
}
"""


def norm(value) -> str:
    return re.sub(r"[\s:]+$", "", re.sub(r"\s+", " ", str(value)).strip().lower())


def cy(node: dict) -> float:
    return node["y"] + node["h"] / 2


def find_all(texts: list[dict], label: str, kinds=("text", "value")) -> list[dict]:
    wanted = norm(label)
    return [t for t in texts if norm(t["t"]) == wanted and t["kind"] in kinds]


def find_first(texts: list[dict], label: str, **kwargs) -> dict | None:
    found = find_all(texts, label, **kwargs)
    return min(found, key=lambda t: (t["y"], t["x"])) if found else None


def find_control(facts: dict, anchor: dict | None, label: str) -> dict | None:
    wanted, controls = norm(label), facts["controls"]
    for field in ("owner", "label"):
        exact = [c for c in controls if norm(c[field]) == wanted]
        if len(exact) == 1:
            return exact[0]
    contains = [c for c in controls if wanted in c["owner"] or wanted in c["label"]]
    if len(contains) == 1:
        return contains[0]
    if anchor is None or not controls:
        return None
    nearest = min(controls, key=lambda c: abs(c["y"] + c["h"] / 2 - cy(anchor)))
    if abs(nearest["y"] + nearest["h"] / 2 - cy(anchor)) > ROW_TOLERANCE_PX * 2:
        return None
    return nearest


class Checks:
    """Collects (name, passed) pairs per category so misses can be reported by name."""

    def __init__(self):
        self.results: dict[str, list[tuple[str, bool]]] = {}

    def add(self, category: str, name: str, passed: object) -> None:
        self.results.setdefault(category, []).append((name, bool(passed)))

    def summary(self) -> dict:
        out = {}
        for category, items in self.results.items():
            hits = sum(ok for _, ok in items)
            out[category] = {
                "points": [hits, len(items)],
                "score": hits / len(items),
                "missed": [name for name, ok in items if not ok],
            }
        return out


def stepper_node(texts: list[dict], label: str) -> dict | None:
    found = find_all(texts, label, kinds=("text",))
    return min(found, key=lambda t: (t["x"], t["y"])) if found else None


def toggle_node(
    texts: list[dict], label: str, y_min: float, stepper: dict | None
) -> dict | None:
    below = [
        t
        for t in find_all(texts, label, kinds=("text",))
        if t["y"] > y_min and t is not stepper
    ]
    return min(below, key=lambda t: (t["y"], t["x"])) if below else None


@dataclass
class Located:
    """The screen's landmarks found in the page, each None when absent."""

    heading: dict[str, dict | None]
    right: dict | None
    steps: dict[str, dict | None]
    anchors: dict[str, dict | None]
    controls: dict[str, dict | None]
    y_metadata: float
    y_determine: float


def locate(facts: dict, expected: dict) -> Located:
    texts = facts["texts"]
    heading = {
        label: find_first(texts, label, kinds=("text",)) for label in expected["headings"]
    }
    right_nodes = [
        n
        for label in expected["right_headings"]
        if (n := find_first(texts, label, kinds=("text",)))
    ]
    y_metadata = (heading["Metadata"] or {}).get("y", float("-inf"))
    y_determine = (heading["Determine applicable procedures"] or {}).get("y", float("inf"))
    steps = {label: stepper_node(texts, label) for label in expected["steps"]}
    anchors = {
        t["label"]: toggle_node(texts, t["label"], y_determine, steps.get(t["label"]))
        for t in expected["toggles"]
    }
    return Located(
        heading=heading,
        right=min(right_nodes, key=lambda n: n["x"]) if right_nodes else None,
        steps=steps,
        anchors=anchors,
        controls={
            label: find_control(facts, anchor, label) for label, anchor in anchors.items()
        },
        y_metadata=y_metadata,
        y_determine=y_determine,
    )


def before(first: dict | None, second: dict | None, axis: str) -> bool:
    return first is not None and second is not None and first[axis] < second[axis]


def content_checks(checks: Checks, facts: dict, expected: dict, found: Located) -> None:
    texts = facts["texts"]
    for label, node in found.steps.items():
        checks.add("content", f"step {label}", node)
    for field in expected["fields"]:
        label_hit = find_first(texts, field["label"], kinds=("text",))
        value_hit = find_first(texts, field["value"], kinds=("text", "value"))
        checks.add("content", f"field {field['label']}", label_hit and value_hit)
    for label, node in found.heading.items():
        checks.add("content", f"heading {label}", node)
    checks.add("content", "right heading", found.right)
    hint = norm(expected["input_hint"])
    checks.add("content", "input hint", any(hint in t["t"] for t in texts))
    checks.add("content", "ATLAS note", norm(expected["initial_message"]) in facts["body"])


def row_aligned(label_node: dict | None, value_nodes: list[dict]) -> bool:
    if label_node is None:
        return False
    return any(
        abs(cy(v) - cy(label_node)) <= ROW_TOLERANCE_PX
        and v["x"] > label_node["x"] + label_node["w"] - 2
        for v in value_nodes
    )


def stepper_in_one_column(nodes: list[dict]) -> bool:
    if len(nodes) < MIN_STEPS_FOR_COLUMN:
        return False
    return max(n["x"] for n in nodes) - min(n["x"] for n in nodes) <= COLUMN_TOLERANCE_PX


def layout_checks(checks: Checks, facts: dict, expected: dict, found: Located) -> None:
    texts = facts["texts"]
    checks.add(
        "layout",
        "left before center",
        before(found.heading["Procedures"], found.heading["Engagement 1"], "x"),
    )
    checks.add(
        "layout",
        "center before right",
        before(found.heading["Engagement 1"], found.right, "x"),
    )

    steps = [(label, found.steps[label]) for label in expected["steps"]]
    for (_, first), (name, second) in zip(steps, steps[1:], strict=False):
        checks.add("layout", f"step order {name}", before(first, second, "y"))
    checks.add(
        "layout", "stepper in one column", stepper_in_one_column([n for _, n in steps if n])
    )

    metadata_rows = [t for t in texts if found.y_metadata < t["y"] < found.y_determine]
    for field in expected["fields"]:
        label_node = find_first(metadata_rows, field["label"], kinds=("text",))
        checks.add(
            "layout",
            f"row {field['label']}",
            row_aligned(label_node, find_all(texts, field["value"])),
        )

    toggles = [(t["label"], found.anchors[t["label"]]) for t in expected["toggles"]]
    for (_, first), (name, second) in zip(toggles, toggles[1:], strict=False):
        checks.add("layout", f"toggle order {name}", before(first, second, "y"))


def state_checks(checks: Checks, expected: dict, found: Located) -> None:
    for spec in expected["toggles"]:
        control = found.controls[spec["label"]]
        checks.add(
            "states",
            f"toggle {spec['label']}",
            control is not None and control["state"] is spec["on"],
        )
    selected = found.steps[expected["selected_step"]]
    others = [n for n in found.steps.values() if n and n is not selected]
    styled = (
        selected is not None
        and bool(others)
        and all(selected["sig"] != o["sig"] for o in others)
    )
    checks.add("states", "active step styled", styled)


def interaction_checks(
    checks: Checks, expected: dict, found: Located, after: dict | None
) -> None:
    target = expected["interaction"]["toggle"]
    wanted = {t["label"]: t["on"] for t in expected["toggles"]}
    flipped = unchanged = message = False
    if after is not None:
        now = {
            label: find_control(after, anchor, label)
            for label, anchor in found.anchors.items()
        }
        states = {label: c["state"] for label, c in now.items() if c is not None}
        if len(states) == len(now):
            flipped = states[target] is (not wanted[target])
            unchanged = all(
                states[label] is state for label, state in wanted.items() if label != target
            )
        message = norm(expected["interaction"]["message"]) in after["body"]
    checks.add("interaction", f"{target} flips", flipped)
    checks.add("interaction", "other switches unchanged", unchanged)
    checks.add("interaction", "success message shown", message)


def screen_found(facts: dict, expected: dict, found: Located) -> bool:
    field_labels = sum(
        find_first(facts["texts"], f["label"], kinds=("text",)) is not None
        for f in expected["fields"]
    )
    switches = sum(c is not None for c in found.controls.values())
    return (
        norm(expected["wrong_screen_marker"]) not in facts["body"]
        and field_labels >= MIN_FIELD_LABELS_FOR_SCREEN
        and switches >= MIN_SWITCHES_FOR_SCREEN
    )


def score(facts: dict, expected: dict, after: dict | None = None) -> dict:
    """Score collected page facts. `after` holds the facts after flipping the target switch."""
    found, checks = locate(facts, expected), Checks()
    content_checks(checks, facts, expected, found)
    layout_checks(checks, facts, expected, found)
    state_checks(checks, expected, found)
    interaction_checks(checks, expected, found, after)
    return {"screen_found": int(screen_found(facts, expected, found)), **checks.summary()}


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
        elif name == "Skill" and "excalidraw" in str(args.get("skill", "")).lower():
            counts["skill"] += 1
        elif name == "Bash" and "excalidraw.py" in str(args.get("command", "")):
            counts["script"] += 1
        else:
            counts["other"] += 1
    return counts


GRADING_FILES = ("expected.json", "check_prototype")


def peeked(output: Path) -> bool:
    return any(
        name in json.dumps(use.get("input"))
        for use in tool_uses(output)
        for name in GRADING_FILES
    )


def find_browser() -> str | None:
    if os.environ.get("CCBENCH_CHROME"):
        return os.environ["CCBENCH_CHROME"]
    cache = Path.home() / "Library/Caches/ms-playwright"
    patterns = [
        str(
            cache
            / "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        str(cache / "chromium-*/chrome-linux/chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def browse(html: Path, expected: dict, screenshot: Path, browser_path: str) -> dict:
    from playwright.sync_api import sync_playwright  # ty: ignore[unresolved-import]

    blocked: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=browser_path, headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        def gate(route):
            if route.request.url.startswith(("file:", "data:", "about:", "blob:")):
                route.continue_()
            else:
                blocked.append(route.request.url)
                route.abort()

        page.route("**/*", gate)
        page.goto(html.resolve().as_uri())
        page.wait_for_timeout(500)
        facts = page.evaluate(COLLECT_JS)
        page.screenshot(path=str(screenshot))
        after = None
        stepper = stepper_node(facts["texts"], expected["interaction"]["toggle"])
        determine = find_first(
            facts["texts"], "Determine applicable procedures", kinds=("text",)
        )
        anchor = toggle_node(
            facts["texts"],
            expected["interaction"]["toggle"],
            determine["y"] if determine else float("-inf"),
            stepper,
        )
        target = find_control(facts, anchor, expected["interaction"]["toggle"])
        if target is not None:
            page.evaluate(
                '(i) => document.querySelector(`[data-bench-control="${i}"]`).click()',
                target["index"],
            )
            page.wait_for_timeout(400)
            after = page.evaluate(COLLECT_JS)
        browser.close()
    result = score(facts, expected, after)
    result.update(
        external_requests=blocked,
        page_errors=page_errors,
        overflow={"scrollWidth": facts["scrollWidth"], "scrollHeight": facts["scrollHeight"]},
    )
    return result


COMPARE_METRICS_FILE = Path("metrics/prototype_check.json")


def write_compare_metrics(result: dict) -> None:
    """Contribute metrics to `ccbench compare`; None values render as missing."""
    calls = result["tooling_calls"]
    metrics = {
        "screen_found": {"value": result["screen_found"], "label": "Prototype: screen found", "format": "{:.0f}/1"},
    }
    for category in ("content", "layout", "states", "interaction"):
        metrics[category] = {
            "value": (result.get(category) or {}).get("score"),
            "label": f"Prototype: {category}",
            "format": "{:.0%}",
        }
    metrics["tooling_calls"] = {
        "value": calls["mcp"] + calls["skill"] + calls["script"],
        "label": "Prototype: skill/MCP/script calls",
        "format": "{:.0f}",
    }
    COMPARE_METRICS_FILE.parent.mkdir(exist_ok=True)
    COMPARE_METRICS_FILE.write_text(json.dumps({"eval": "prototype_check", "metrics": metrics}, indent=2))


def main() -> None:
    expected = json.loads(Path("expected.json").read_text())
    html = Path("project/index.html")
    result: dict = {"status": "completed", "html_written": html.exists()}
    if html.exists():
        browser_path = find_browser()
        if browser_path is None:
            print("No Chrome found; set CCBENCH_CHROME", file=sys.stderr)
            sys.exit(1)
        result.update(browse(html, expected, Path("prototype.png"), browser_path))
    else:
        empty = score({"texts": [], "controls": [], "body": ""}, expected)
        result.update(empty, screen_found=0)
    result["tooling_calls"] = tooling_calls(Path("output.json"))
    result["peeked"] = peeked(Path("output.json"))
    Path("prototype_check.json").write_text(json.dumps(result, indent=2))
    write_compare_metrics(result)
    line = " ".join(
        f"{c}={result[c]['score']:.2f}"
        for c in ("content", "layout", "states", "interaction")
        if c in result
    )
    print(f"screen_found={result['screen_found']} {line} tools={result['tooling_calls']}")


if __name__ == "__main__":
    main()
