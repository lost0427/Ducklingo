from pathlib import Path
from tomllib import load


def load_config(path: str | Path) -> dict:
    with Path(path).open("rb") as file:
        return load(file)


def project_path(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path
