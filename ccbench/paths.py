from pathlib import Path

CCBENCH_DIR = Path(__file__).resolve().parent.parent
CCBENCH_HOME = Path.home() / ".ccbench"

FORGE = CCBENCH_DIR / "config_forge"
TASKS = CCBENCH_DIR / "tasks"
EXPERIMENTS = CCBENCH_DIR / "experiments"
RESULTS = CCBENCH_HOME / "results"
EVALS = CCBENCH_DIR / "evals"

STAGING_SCRIPT = "staging.sh"
SCRIPT_NAMES = {STAGING_SCRIPT, "setup.sh", "run.sh"}

CCBENCH_IGNORE = ".ccbenchignore"
DEFAULT_CCBENCH_IGNORE_PATTERNS = ("__pycache__", ".npm")
