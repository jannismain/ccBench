import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from .compare import cmd_compare
from .experiment import run_experiment
from .history import (
    compare_history,
    default_history_dir,
    diff_runs,
    find_candidates,
    load_policy,
    resolve_run,
)
from .retry import retry as retry_results

app = typer.Typer(
    name="ccbench",
    help="Run and analyze ccBench experiments",
    no_args_is_help=True,
)

results_app = typer.Typer(
    help="Find and inspect recorded run provenance.",
    no_args_is_help=True,
)
app.add_typer(results_app, name="results")

_SUBCOMMANDS = frozenset({"run", "compare", "retry", "results"})
_ROOT_OPTIONS = frozenset({"--help", "-h", "--install-completion", "--show-completion"})


@app.command()
def run(
    experiment: Annotated[str | None, typer.Argument()] = None,
    shard: Annotated[list[str] | None, typer.Option("--shard")] = None,
    eval_: Annotated[list[str] | None, typer.Option("--eval")] = None,
    variant: Annotated[str | None, typer.Option()] = None,
    task: Annotated[str | None, typer.Option()] = None,
    skip_run: Annotated[bool, typer.Option("--skip-run/--no-skip-run")] = False,
    results_dir: Annotated[str | None, typer.Option()] = None,
    runs: Annotated[
        int | None,
        typer.Option(
            "-n",
            "--runs",
            min=1,
            help="Repeat each task/variant; overrides YAML runs (default: 1).",
        ),
    ] = None,
) -> None:
    """Run an experiment."""
    run_experiment(
        experiment,
        shards=tuple(shard or []),
        evals=tuple(eval_ or []),
        variant=variant,
        task=task,
        skip_run=skip_run,
        results_dir=results_dir,
        runs=runs,
    )


@app.command()
def compare(
    result_dirs: Annotated[list[str] | None, typer.Argument()] = None,
    across: Annotated[bool, typer.Option("--across/--no-across")] = False,
    json_output: Annotated[bool, typer.Option("--json/--no-json")] = False,
    baseline_history: Annotated[bool, typer.Option("--baseline-history")] = False,
    baseline: Annotated[str, typer.Option()] = "baseline",
    history_dir: Annotated[Path | None, typer.Option()] = None,
    policy: Annotated[Path | None, typer.Option()] = None,
    report: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Compare experiment results."""
    if baseline_history:
        try:
            compare_history(
                list(result_dirs or []),
                baseline,
                history_dir or default_history_dir(),
                load_policy(policy),
                report,
                json_output,
            )
        except (ValueError, OSError) as exc:
            raise typer.BadParameter(str(exc)) from exc
    else:
        if policy or report or history_dir or baseline != "baseline":
            raise typer.BadParameter("History options require --baseline-history")
        cmd_compare(list(result_dirs or []), across=across, json_output=json_output)


@results_app.command("find")
def results_find(
    reference: Annotated[str, typer.Option("--for")],
    baseline: Annotated[str, typer.Option()] = "baseline",
    history_dir: Annotated[Path | None, typer.Option()] = None,
    policy: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Explain eligibility of historical baselines for one reference run."""
    directory = history_dir or default_history_dir()
    try:
        found = find_candidates(
            resolve_run(reference, directory), directory, baseline, load_policy(policy)
        )
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(found, indent=2))


@results_app.command("diff")
def results_diff(
    left: str,
    right: str,
    history_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Show manifests and starting-file text differences using paths or run IDs."""
    directory = history_dir or default_history_dir()
    try:
        result = diff_runs(resolve_run(left, directory), resolve_run(right, directory))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2))


@app.command()
def retry(
    result_dirs: Annotated[list[str] | None, typer.Argument()] = None,
    step: Annotated[list[str] | None, typer.Option("--step")] = None,
    task: Annotated[list[str] | None, typer.Option("--task")] = None,
) -> None:
    """Retry failed or selected result steps."""
    retry_results(list(result_dirs or []), steps=tuple(step or []), tasks=tuple(task or []))


def _preprocess_tokens(tokens: list[str]) -> list[str]:
    """Inject 'run' subcommand for legacy default-command invocations."""
    if not tokens or tokens[0] in _SUBCOMMANDS or tokens[0] in _ROOT_OPTIONS:
        return tokens
    return ["run"] + tokens


def main(tokens: list[str] | None = None) -> None:
    load_dotenv(".env")
    if tokens is None:
        tokens = sys.argv[1:]
    app(_preprocess_tokens(tokens))
