from pathlib import Path

root = Path(__file__).parent


def test_contamination():
    claude_output = (root / "output.json").read_text().lower()
    assert "codewars" not in claude_output, (
        "The output contains the word 'codewars', which means the model has likely seen this task during training."
    )
