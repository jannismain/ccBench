import json
import shutil
from copy import deepcopy

import pytest
import yaml
from typer.testing import CliRunner

from ccbench.cli import app
from ccbench.compare import build_task_results, resolve_task_dirs
from ccbench.experiment import run_experiment
from ccbench.history import assess_candidate, discover_runs, load_policy
from ccbench.provenance import MANIFEST, archive_before_retry, load_manifest, snapshot_tree


class TestSnapshots:
    def test_nonsecret_env_changes_affect_identity(self, tmp_path):
        """Secret rotation is ignored while nonsecret .env settings remain identifiable."""
        source = tmp_path / "source"
        source.mkdir()
        env = source / ".env"
        env.write_text("MODEL=small\nAPI_KEY=private-value\n")
        first = snapshot_tree(source, tmp_path / "objects", {})
        env.write_text("MODEL=small\nAPI_KEY=other-secret\n")
        assert snapshot_tree(source, tmp_path / "objects", {})["digest"] == first["digest"]
        env.write_text("MODEL=large\nAPI_KEY=other-secret\n")
        assert snapshot_tree(source, tmp_path / "objects", {})["digest"] != first["digest"]

    def test_content_modes_and_secret_exclusions(self, tmp_path):
        """Identity ignores timestamps but detects content/mode changes without secrets."""
        source = tmp_path / "source"
        source.mkdir()
        file = source / "main.py"
        file.write_text("print(1)\n")
        (source / ".env").write_text("API_KEY=private-value\n")
        first = snapshot_tree(source, tmp_path / "objects", {})
        file.touch()
        assert snapshot_tree(source, tmp_path / "objects", {}) == first
        file.chmod(0o755)
        assert snapshot_tree(source, tmp_path / "objects", {})["digest"] != first["digest"]
        file.write_text("print(2)\n")
        assert (
            snapshot_tree(source, tmp_path / "objects", {})["files"]["main.py"]
            != first["files"]["main.py"]
        )
        assert ".env" not in first["files"]
        assert all(
            b"private-value" not in p.read_bytes() for p in (tmp_path / "objects").iterdir()
        )


class TestHistoricalResults:
    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        """Run real local shards with controlled tool versions and no external services."""
        task = tmp_path / "tasks" / "demo"
        task.mkdir(parents=True)
        (task / "project").mkdir()
        (task / "project" / "main.py").write_text("print(1)\n")
        (task / "run.sh").write_text("echo completed\n")
        base = tmp_path / "forge" / "base"
        base.mkdir(parents=True)
        (base / "metadata.yaml").write_text(
            yaml.safe_dump(
                {
                    "tools": {"claude_code": {"command": ["${FAKE_TOOL}", "--version"]}},
                }
            )
        )
        tool = tmp_path / "fake-tool"
        tool.write_text("#!/bin/sh\necho 'Claude Code 2.1.262'\n")
        tool.chmod(0o755)
        monkeypatch.setenv("FAKE_TOOL", str(tool))
        extra = tmp_path / "forge" / "extra"
        (extra / "project").mkdir(parents=True)
        (extra / "project" / "AGENTS.md").write_text("Be concise.\n")
        evaluator = tmp_path / "evals" / "score"
        evaluator.mkdir(parents=True)
        (evaluator / "post.sh").write_text("echo '{\"score\": 1}' > exact_answer.json\n")
        monkeypatch.setattr("ccbench.paths.TASKS", task.parent)
        monkeypatch.setattr("ccbench.paths.FORGE", base.parent)
        monkeypatch.setattr("ccbench.paths.EVALS", evaluator.parent)
        config = tmp_path / "experiment.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "tasks": ["demo"],
                    "configs": ["base"],
                    "variants": {"baseline": [], "new": ["extra"]},
                    "evals": ["score"],
                }
            )
        )
        return config, tmp_path / "results", tool

    def run_variant(self, workspace, variant):
        config, results, _ = workspace
        root = run_experiment(str(config), variant=variant, results_dir=str(results))
        return root / "tasks" / f"demo_{variant}"

    def test_version_policy_and_saved_comparison(self, workspace, tmp_path):
        """Only explicitly permitted runtime differences admit historical samples."""
        baseline = self.run_variant(workspace, "baseline")
        workspace[2].write_text("#!/bin/sh\necho 'Claude Code 2.1.266'\n")
        current = self.run_variant(workspace, "new")
        old = load_manifest(baseline)
        new = load_manifest(current)
        assert old["run_id"] != new["run_id"]
        assert old["state"] == "completed"
        assert old["runtime"]["tools"]["claude_code"]["version"] == "2.1.262"
        assert assess_candidate(new, old, "baseline", {})["eligible"] is False
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(
            yaml.safe_dump(
                {
                    "tools": {"claude_code": {"min": "2.1.262", "max_exclusive": "2.2.0"}},
                }
            )
        )
        policy = load_policy(policy_path)
        assessment = assess_candidate(new, old, "baseline", policy)
        assert assessment["eligible"], assessment
        report = tmp_path / "report.json"
        result = CliRunner().invoke(
            app,
            [
                "compare",
                str(current),
                "--baseline-history",
                "--history-dir",
                str(workspace[1]),
                "--policy",
                str(policy_path),
                "--report",
                str(report),
            ],
        )
        assert result.exit_code == 0, result.output
        saved = json.loads(report.read_text())
        assert set(saved["run_ids"]) == {old["run_id"], new["run_id"]}
        assert saved["policy"] == policy
        assert saved["comparison"]["tasks"]["demo"]["sample_counts"] == {
            "baseline": 1,
            "new": 1,
        }

    def test_content_change_rejects_baseline_and_duplicates_are_deduplicated(self, workspace):
        """Matching labels cannot hide changed shard contents or multiply copied samples."""
        baseline = self.run_variant(workspace, "baseline")
        shutil.copytree(baseline, workspace[1] / "copied")
        manifests = discover_runs(workspace[1])
        assert len(manifests) == 1
        base = workspace[0].parent / "forge" / "base"
        (base / "instruction.txt").write_text("New behavior\n")
        current = self.run_variant(workspace, "new")
        result = assess_candidate(
            load_manifest(current), load_manifest(baseline), "baseline", {}
        )
        assert not result["eligible"]
        assert any("recipe" in item["field"] for item in result["differences"])

    def test_staging_and_prepared_snapshots(self, workspace):
        """Materialized task inputs and staging outputs survive temporary cleanup."""
        extra = workspace[0].parent / "forge" / "extra"
        (extra / "staging.sh").write_text("echo generated > project/generated.txt\n")
        current = self.run_variant(workspace, "new")
        manifest = load_manifest(current)
        assert "project/generated.txt" in manifest["prepared"]["files"]
        assert any(
            "project/generated.txt" in layer["snapshot"]["files"]
            for layer in manifest["layers"]
        )
        assert (current / MANIFEST).exists()

    def test_retry_archives_original_and_excludes_mutated_sample(self, workspace):
        """Retries preserve old evidence without presenting the mutation as a fresh sample."""
        baseline = self.run_variant(workspace, "baseline")
        current = self.run_variant(workspace, "new")
        old = load_manifest(baseline)
        archive_before_retry(baseline)
        updated = load_manifest(baseline)
        assert updated["state"] == "retried"
        archive = baseline / updated["retry_history"][0]["archive"]
        assert json.loads((archive / "manifest.json").read_text()) == old
        assert not assess_candidate(load_manifest(current), updated, "baseline", {})[
            "eligible"
        ]

    def test_plain_compare_separates_versions_and_deduplicates(self, workspace):
        """Ordinary comparison never silently pools differing runtime identities."""
        first = self.run_variant(workspace, "baseline")
        workspace[2].write_text("#!/bin/sh\necho 'Claude Code 2.1.266'\n")
        second = self.run_variant(workspace, "baseline")
        entries = resolve_task_dirs([str(first), str(second), str(first)], across=False)
        grouped = build_task_results(entries)
        assert len(grouped["demo"]["variants"]) == 2
        assert sorted(grouped["demo"]["sample_counts"].values()) == [1, 1]

    @pytest.mark.parametrize(
        "mutation", ["unknown_tool", "evaluator", "drift", "task", "state", "missing"]
    )
    def test_incompatible_evidence_is_rejected(self, workspace, mutation):
        """Unknown, changed, and incomplete inputs cannot qualify automatically."""
        old = load_manifest(self.run_variant(workspace, "baseline"))
        current = load_manifest(self.run_variant(workspace, "new"))
        changed = deepcopy(old)
        if mutation == "unknown_tool":
            changed["runtime"]["tools"]["claude_code"]["version"] = None
        elif mutation == "evaluator":
            changed["evals"][0]["source"]["digest"] = "different"
        elif mutation == "drift":
            changed["runtime_after"]["tools"]["claude_code"]["version"] = "2.1.999"
        elif mutation == "task":
            changed["task_source"]["digest"] = "different"
        elif mutation == "state":
            changed["state"] = "prepared"
        else:
            changed = {}
        assert not assess_candidate(current, changed, "baseline", {})["eligible"]

    def test_version_range_does_not_accept_generated_file_changes(self, workspace):
        """Accepting a tool version never implicitly ignores its generated files."""
        old = load_manifest(self.run_variant(workspace, "baseline"))
        current = load_manifest(self.run_variant(workspace, "new"))
        old["runtime"]["tools"]["claude_code"]["version"] = "2.1.266"
        old["runtime_after"] = deepcopy(old["runtime"])
        old["prepared"]["files"]["project/main.py"]["sha256"] = "changed"
        policy = {"tools": {"claude_code": {"min": "2.1.262", "max_exclusive": "2.2.0"}}}
        result = assess_candidate(current, old, "baseline", policy)
        assert not result["eligible"]
        assert any(
            item["field"] == "prepared/project/main.py" and not item["accepted"]
            for item in result["differences"]
        )

    def test_find_and_diff_cli(self, workspace):
        """Discovery accepts permanent IDs and file diffs include retained input content."""
        old = self.run_variant(workspace, "baseline")
        new = self.run_variant(workspace, "new")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "results",
                "find",
                "--for",
                load_manifest(new)["run_id"],
                "--history-dir",
                str(workspace[1]),
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)[0]["eligible"]
        result = runner.invoke(app, ["results", "diff", str(old), str(new)])
        assert result.exit_code == 0, result.output
        assert "Be concise." in result.output

    @pytest.mark.parametrize(
        "policy", [{"unknown": True}, {"tools": {"claude_code": {"min": "oops"}}}]
    )
    def test_invalid_policy_rejected(self, tmp_path, policy):
        """Typos in compatibility rules never silently relax matching."""
        path = tmp_path / "policy.yaml"
        path.write_text(yaml.safe_dump(policy))
        with pytest.raises(ValueError):
            load_policy(path)
