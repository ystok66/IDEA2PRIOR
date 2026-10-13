"""Resolve resources from either a checkout or an installed wheel."""
from importlib.resources import files
from pathlib import Path


def benchmark_directory(difficulty=None):
    source = Path(__file__).resolve().parents[1] / "datasets"
    if (source / "easy/manifest.json").is_file():
        directory = source
    else:
        directory = Path(str(files("paperarena_benchmarks")))
    if difficulty is None:
        return directory
    difficulty = difficulty.lower()
    if difficulty == "mdeium":
        difficulty = "medium"
    if difficulty not in ("easy", "medium", "hard"):
        raise ValueError("Unknown benchmark difficulty")
    return directory / difficulty
