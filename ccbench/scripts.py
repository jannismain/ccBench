import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

from .log import log

STATUS_FILE = ".ccbench-status.json"


def run_script_with_env_capture(
    script: Path, working_dir: Path, env: dict
) -> tuple[int, dict]:
    """
    Run a script with interactive terminal support while capturing output and env.

    Uses a pseudo-terminal to maintain interactivity for prompts like `read -p`.
    Returns (return_code, updated_env).
    """
    log_file = script.with_suffix(".log")
    env_file = working_dir / ".env_capture"
    script.chmod(script.stat().st_mode | 0o755)

    wrapper = f"""
source {script.name}
__exit_code=$?
env > {env_file}
exit $__exit_code
"""

    master_fd, slave_fd = pty.openpty()
    winsize = struct.pack("HHHH", 40, 120, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    stdin_is_tty = sys.stdin.isatty()
    old_settings = None

    try:
        if stdin_is_tty:
            stdin_fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)

        process = subprocess.Popen(
            ["bash", "-c", wrapper],
            cwd=working_dir,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
        )
        os.close(slave_fd)
        capture_pty_output(master_fd, process, log_file, stdin_is_tty)
        process.wait()
        return_code = process.returncode

    finally:
        if old_settings is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
        os.close(master_fd)

    record_script_status(script, return_code)
    return return_code, read_captured_env(env_file, env)


def load_script_statuses(working_dir: Path) -> dict:
    status_file = working_dir / STATUS_FILE
    if not status_file.exists():
        return {"scripts": {}}
    try:
        data = json.loads(status_file.read_text())
    except json.JSONDecodeError:
        log.warning(f"Failed to parse script status file: {status_file}")
        return {"scripts": {}}
    if not isinstance(data, dict):
        return {"scripts": {}}
    data.setdefault("scripts", {})
    return data


def record_script_status(script: Path, return_code: int) -> None:
    status_file = script.parent / STATUS_FILE
    data = load_script_statuses(script.parent)
    scripts = data.setdefault("scripts", {})
    entry = scripts.setdefault(script.name, {"attempts": []})
    finished_at = datetime.now().isoformat(timespec="seconds")
    attempt = {
        "finished_at": finished_at,
        "return_code": return_code,
    }
    entry.setdefault("attempts", []).append(attempt)
    entry["last_finished_at"] = finished_at
    entry["return_code"] = return_code
    entry["log"] = script.with_suffix(".log").name
    status_file.write_text(json.dumps(data, indent=2, sort_keys=True))


def capture_pty_output(
    master_fd: int, process: subprocess.Popen, log_file: Path, stdin_is_tty: bool
) -> None:
    with log_file.open("wb") as log_f:
        while True:
            ready_read = poll_ready_streams(master_fd, stdin_is_tty)
            forward_stdin_if_ready(master_fd, process, ready_read, stdin_is_tty)
            if master_fd in ready_read and not forward_output(master_fd, log_f):
                break
            if process.poll() is not None:
                drain_output(master_fd, log_f)
                break


def poll_ready_streams(master_fd: int, stdin_is_tty: bool) -> list:
    if stdin_is_tty:
        ready_read, _, _ = select.select([sys.stdin, master_fd], [], [], 0.1)
    else:
        ready_read, _, _ = select.select([master_fd], [], [], 0.1)
    return ready_read


def forward_stdin_if_ready(
    master_fd: int,
    process: subprocess.Popen,
    ready_read: list,
    stdin_is_tty: bool,
) -> None:
    if not stdin_is_tty or sys.stdin not in ready_read:
        return
    try:
        data = os.read(sys.stdin.fileno(), 1024)
        if not data:
            return
        if b"\x03" in data:
            process.send_signal(signal.SIGINT)
        else:
            os.write(master_fd, data)
    except OSError:
        pass


def forward_output(master_fd: int, log_f) -> bool:
    try:
        data = os.read(master_fd, 1024)
        if not data:
            return False
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        log_f.write(data)
        return True
    except OSError:
        return False


def drain_output(master_fd: int, log_f) -> None:
    try:
        while True:
            data = os.read(master_fd, 1024)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            log_f.write(data)
    except OSError:
        pass


def read_captured_env(env_file: Path, env: dict) -> dict:
    new_env = env.copy()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                new_env[key] = value
        env_file.unlink()
    return new_env


def run_scripts_with_env_propagation(
    script_pattern: str, working_dir: Path, env: dict, stop_on_failure: bool = True
) -> tuple[bool, dict]:
    """Run scripts matching pattern in alphabetical order, propagating environment."""
    all_succeeded = True
    for script in sorted(working_dir.glob(script_pattern)):
        log.info(f"Running {script.name}")
        return_code, env = run_script_with_env_capture(script, working_dir, env)
        if return_code != 0:
            log.error(f"Script {script.name} failed with code {return_code}")
            all_succeeded = False
            if stop_on_failure:
                return False, env
    return all_succeeded, env


def ensure_project_git_repo(project_dir: Path) -> None:
    """Ensure the agent workspace has its own git repo without mutating imported repos."""
    project_root = project_dir.resolve()
    existing_repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if existing_repo.returncode == 0:
        repo_root = Path(existing_repo.stdout.strip()).resolve()
        if repo_root == project_root:
            log.info(f"Project directory already has a git repo at {project_dir}")
            return

    commands = (
        ["git", "init", "--quiet"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=ccBench",
            "-c",
            "user.email=ccbench@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "ccbench initial state",
        ],
    )
    for command in commands:
        result = subprocess.run(command, cwd=project_dir, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to initialize git repo in {project_dir}: "
                f"{' '.join(command)}\n{result.stderr.strip()}"
            )
