import sys
from typing import Annotated

import typer
from dotenv import load_dotenv

from .compare import cmd_compare
from .experiment import run_experiment
from .retry import retry as retry_results

app = typer.Typer(
    name="ccbench",
    help="Run and analyze ccBench experiments",
    no_args_is_help=True,
)

_SUBCOMMANDS = frozenset({"run", "compare", "retry"})
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
    )


@app.command()
def compare(
    result_dirs: Annotated[list[str] | None, typer.Argument()] = None,
    across: Annotated[bool, typer.Option("--across/--no-across")] = False,
    json_output: Annotated[bool, typer.Option("--json/--no-json")] = False,
) -> None:
    """Compare experiment results."""
    cmd_compare(list(result_dirs or []), across=across, json_output=json_output)


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
