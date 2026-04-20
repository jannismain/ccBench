from cyclopts import App
from dotenv import load_dotenv

from .compare import cmd_compare
from .experiment import run_experiment
from .retry import retry as retry_results


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


def retry(
    *result_dirs: str,
    step: tuple[str, ...] = (),
    task: tuple[str, ...] = (),
) -> None:
    """Retry failed or selected result steps."""
    retry_results(list(result_dirs), steps=step, tasks=task)


def build_app() -> App:
    app = App(name="ccbench", help="Run and analyze ccBench experiments")
    app.default(run)
    app.command(run, name="run", help="Run an experiment")
    app.command(compare, name="compare", help="Compare experiment results")
    app.command(retry, name="retry", help="Retry failed or selected result steps")
    return app


app = build_app()


def main(tokens: list[str] | None = None) -> None:
    load_dotenv(".env")
    app(tokens)
