#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pyyaml",
# ]
# ///

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

FORGE = Path(__file__).with_name("config_forge")
TASKS = Path(__file__).with_name("tasks")
ROOT = Path(__file__).with_name("experiments")

# Create experiment directory
experiment_file = ROOT / sys.argv[1]
if not experiment_file.exists():
    print(f"Experiment '{experiment_file}' not found.")
    exit(1)
experiment_name = experiment_file.stem
experiment_root = (
    ROOT
    / f"{datetime.now().isoformat(sep='_', timespec='seconds').replace(':', '').replace('-', '')}_{experiment_name}"
)
experiment_root.mkdir(parents=True, exist_ok=True)
experiment_file.copy_into(experiment_root)

with experiment_file.open() as f:
    experiment_config = yaml.safe_load(f)

experiment_agent_root = experiment_root / "project"
experiment_agent_root.mkdir()

# copy all files of each config shard into the project directory
for config_shard in experiment_config["configs"]:
    d = Path(FORGE / config_shard)
    for f in d.glob("*"):
        f.copy_into(experiment_agent_root)

# copy all task files into the project directory
task_dir = TASKS / experiment_config["task"]
for f in task_dir.glob("*"):
    f.copy_into(experiment_agent_root)

# move entrypoint and prompt into experiment root
entrypoint = experiment_agent_root / "run.sh"
if not entrypoint.exists():
    sys.exit(f"Experiment entrypoint '{entrypoint}' not found.")
entrypoint.move_into(experiment_root)

prompt_file = experiment_agent_root / "prompt.md"
if not prompt_file.exists():
    sys.exit(f"Experiment prompt file '{prompt_file}' not found.")
prompt_file.move_into(experiment_root)

os.chdir(experiment_root)

# create INITIAL_FILES manifest
Path("INITIAL_FILES").write_text(
    "\n".join(
        [str(f.relative_to(experiment_root)) for f in experiment_agent_root.glob("*")]
    )
    + "\n"
)
os.system("chmod +x run.sh")
os.system("git init && git add . && git commit -m 'Initial commit'")
os.system("./run.sh")

# Evaluate results
os.system("cloc --exclude-list-file=INITIAL_FILES project")
