from cyclopts import App
from dotenv import load_dotenv

from .compare import cmd_compare
from .experiment import run_experiment


def run(
    experiment: str | None = None,
    *,
    shard: tuple[str, ...] = (),
    eval: tuple[str, ...] = (),
    variant: str | None = None,
    task: str | None = None,
    skip_run: bool = False,
    results_dir: str | None = None,
) -> None:
    """Run an experiment."""
    run_experiment(
        experiment,
        shards=shard,
        evals=eval,
        variant=variant,
        task=task,
        skip_run=skip_run,
        results_dir=results_dir,
    )


def compare(
    *result_dirs: str,
    across: bool = False,
    json: bool = False,
) -> None:
    """Compare experiment results."""
    cmd_compare(list(result_dirs), across=across, json_output=json)


def build_app() -> App:
    app = App(name="ccbench", help="Run and analyze ccBench experiments")
    app.default(run)
    app.command(run, name="run", help="Run an experiment")
    app.command(compare, name="compare", help="Compare experiment results")
    return app


app = build_app()


def main(tokens: list[str] | None = None) -> None:
    load_dotenv(".env")
    app(tokens)
