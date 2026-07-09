from pathlib import Path

from project.solution import solve


def test_result():
    puzzle_input = Path("input.txt").read_text().strip()
    assert solve(puzzle_input) == 984
