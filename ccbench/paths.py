from pathlib import Path

CCBENCH_DIR = Path(__file__).resolve().parent.parent

FORGE = CCBENCH_DIR / "config_forge"
TASKS = CCBENCH_DIR / "tasks"
EXPERIMENTS = CCBENCH_DIR / "experiments"
RESULTS = CCBENCH_DIR / "results"
EVALS = CCBENCH_DIR / "evals"

STAGING_SCRIPT = "staging.sh"
SCRIPT_NAMES = {STAGING_SCRIPT, "setup.sh", "run.sh"}

CCBENCH_IGNORE = ".ccbenchignore"
DEFAULT_CCBENCH_IGNORE_PATTERNS = ("__pycache__", ".npm")

