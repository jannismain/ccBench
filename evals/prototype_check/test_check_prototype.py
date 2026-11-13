"""Tests for the prototype_check eval shard.

The checks drive headless Chrome, so these tests need Playwright and a Chrome binary:
`uv run --with playwright pytest evals/prototype_check`. Without them the module is skipped.
"""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

module_path = Path(__file__).with_name("check_prototype.py")
spec = importlib.util.spec_from_file_location("prototype_check_module", module_path)
assert spec is not None
check_prototype = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(check_prototype)

HERE = Path(__file__).parent
# Copy of tasks/excalidraw-prototype/expected.json, so the eval tests stand alone.
EXPECTED = json.loads((HERE / "fixtures/expected.json").read_text())
REFERENCE = (HERE / "fixtures/reference.html").read_text()

BROWSER = check_prototype.find_browser()
pytestmark = pytest.mark.skipif(BROWSER is None, reason="no Chrome binary found")


def run(tmp_path: Path, html: str) -> dict:
    page = tmp_path / "index.html"
    page.write_text(html)
    return check_prototype.browse(page, EXPECTED, tmp_path / "shot.png", BROWSER)


def points(result: dict, category: str) -> int:
    return result[category]["points"][0]


class TestPrototypeCheck:
    def test_reference_page_scores_full_marks(self, tmp_path):
        """A faithful page passes every check, which anchors the point totals."""
        result = run(tmp_path, REFERENCE)

        assert result["screen_found"] == 1
        for category in ("content", "layout", "states", "interaction"):
            assert result[category]["missed"] == [], category
        assert result["external_requests"] == []
        assert result["page_errors"] == []

    def test_flipped_initial_switch_costs_one_state_point(self, tmp_path):
        html = REFERENCE.replace(
            '<input type="checkbox" checked> 01 Leadsheet',
            '<input type="checkbox"> 01 Leadsheet',
        )

        result = run(tmp_path, html)

        assert result["states"]["missed"] == ["toggle 01 Leadsheet"]
        assert result["layout"]["missed"] == []

    def test_unstyled_active_step_is_a_state_miss(self, tmp_path):
        html = REFERENCE.replace("li.active { background: #343a40; color: #fff; }", "")

        assert run(tmp_path, html)["states"]["missed"] == ["active step styled"]

    def test_reordered_stepper_is_a_layout_miss(self, tmp_path):
        html = REFERENCE.replace(
            "<li>01 Leadsheet</li>\n      <li>02 Reconciliation</li>",
            "<li>02 Reconciliation</li>\n      <li>01 Leadsheet</li>",
        )

        missed = run(tmp_path, html)["layout"]["missed"]

        assert "step order 02 Reconciliation" in missed or "step order 01 Leadsheet" in missed

    def test_page_without_behavior_still_flips_but_shows_no_message(self, tmp_path):
        html = REFERENCE.replace(
            "box.addEventListener('change'", "box.addEventListener('never'"
        )

        result = run(tmp_path, html)

        assert points(result, "interaction") == 2
        assert result["interaction"]["missed"] == ["success message shown"]

    def test_tri_state_alternative_is_the_wrong_screen(self, tmp_path):
        html = REFERENCE.replace(
            "Your scoping has been successfully applied.", "Dein Scoping wurde übernommen."
        ).replace("</section>\n</div>", "Dein Scoping wurde übernommen.</section>\n</div>", 1)

        assert run(tmp_path, html)["screen_found"] == 0

    def test_role_switch_elements_are_read_through_aria(self, tmp_path):
        rows = "".join(
            f'<div class="sw"><span role="switch" tabindex="0" aria-checked="{state}" '
            f"onclick=\"this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'true' ? 'false' : 'true');"
            f"document.getElementById('log').insertAdjacentHTML('beforeend','<p>Your scoping has been successfully applied.</p>')\">"
            f"</span> {label}</div>"
            for label, state in [
                ("01 Leadsheet", "true"),
                ("02 Reconciliation", "true"),
                ("03 Additions", "false"),
                ("04a Transfers", "true"),
                ("05 Disposals", "false"),
            ]
        )
        start = REFERENCE.index('<div class="switches">')
        end = REFERENCE.index("</div>", REFERENCE.index("05 Disposals", start)) + len(
            "</div>"
        )
        html = (
            REFERENCE[:start] + f'<div class="switches">{rows}</div>' + REFERENCE[end:]
        ).replace("<script>", "<script>/*")
        html = html.replace("</script>", "*/</script>")

        result = run(tmp_path, html)

        assert result["states"]["missed"] == []
        assert result["interaction"]["missed"] == []

    def test_external_requests_are_blocked_and_reported(self, tmp_path):
        html = REFERENCE.replace(
            "</head>", '<link rel="stylesheet" href="https://cdn.example.com/x.css"></head>'
        )

        assert run(tmp_path, html)["external_requests"] == ["https://cdn.example.com/x.css"]

    def test_script_errors_are_reported(self, tmp_path):
        html = REFERENCE.replace("</body>", "<script>throw new Error('boom')</script></body>")

        assert run(tmp_path, html)["page_errors"]

    def test_missing_page_scores_zero(self):
        result = check_prototype.score({"texts": [], "controls": [], "body": ""}, EXPECTED)

        assert all(
            result[c]["score"] == 0 for c in ("content", "layout", "states", "interaction")
        )
        assert result["screen_found"] == 0


class TestToolingCalls:
    def test_counts_and_peeking(self, tmp_path):
        events = [
            {"type": "assistant", "message": {"content": [b]}}
            for b in (
                {"type": "tool_use", "name": "mcp__excalidraw__describe_scene", "input": {}},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "python3 x/excalidraw.py list f"},
                },
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "../expected.json"},
                },
            )
        ]
        output = tmp_path / "output.json"
        output.write_text("\n".join(json.dumps(e) for e in events))

        assert check_prototype.tooling_calls(output) == {
            "mcp": 1,
            "skill": 0,
            "script": 1,
            "other": 1,
        }
        assert check_prototype.peeked(output) is True


class TestCompareMetrics:
    def test_reports_category_scores_and_tool_calls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        check_prototype.write_compare_metrics(
            {
                "screen_found": 1,
                "content": {"score": 0.5},
                "layout": {"score": 1.0},
                "tooling_calls": {"mcp": 1, "skill": 0, "script": 2, "other": 4},
            }
        )
        written = json.loads((tmp_path / "metrics" / "prototype_check.json").read_text())
        values = {key: metric["value"] for key, metric in written["metrics"].items()}
        assert values == {
            "screen_found": 1,
            "content": 0.5,
            "layout": 1.0,
            "states": None,
            "interaction": None,
            "tooling_calls": 3,
        }
