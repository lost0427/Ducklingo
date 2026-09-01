import threading
from pathlib import Path

from .config import load_config
from .tools import download_file

MODEL_SUFFIXES = {".gguf", ".bin"}
_single = threading.Lock()
_status: dict[str, dict] = {}


def registry_path(root: Path) -> Path:
    custom = Path(root) / "config" / "asr_models.toml"
    return custom if custom.exists() else Path(root) / "config" / "asr_models.example.toml"


def load_registry(root: Path) -> list[dict]:
    return list(load_config(registry_path(root)).get("models", []))


def models_dir(root: Path) -> Path:
    return Path(root) / "data" / "models" / "asr"


def list_models(root: Path) -> list[dict]:
    directory = models_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    items: dict[str, dict] = {}
    for entry in load_registry(root):
        items[entry["id"]] = {
            "id": entry["id"],
            "kind": entry.get("kind", "asr"),
            "name": entry.get("name", entry["id"]),
            "filename": entry["filename"],
            "installed": (directory / entry["filename"]).exists(),
            "manual": False,
        }
    for path in sorted(directory.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower() in MODEL_SUFFIXES
            and not path.name.endswith(".part")
            and path.stem not in items
        ):
            items[path.stem] = {
                "id": path.stem,
                "kind": "asr",
                "name": path.name,
                "filename": path.name,
                "installed": True,
                "manual": True,
            }
    return list(items.values())


def resolve_model(root: Path, model_id: str) -> Path:
    directory = models_dir(root)
    for entry in load_registry(root):
        if entry["id"] == model_id:
            path = directory / entry["filename"]
            if not path.exists():
                raise RuntimeError(f"model not downloaded: {model_id}")
            return path
    directory = models_dir(root)
    if (directory / model_id).exists():
        return directory / model_id
    for suffix in sorted(MODEL_SUFFIXES):
        path = directory / f"{model_id}{suffix}"
        if path.exists():
            return path
    raise RuntimeError(f"model not found: {model_id}")


def resolve_vad(root: Path) -> Path:
    directory = models_dir(root)
    for entry in load_registry(root):
        if entry.get("kind") == "vad":
            path = directory / entry["filename"]
            if path.exists():
                return path
    for path in sorted(directory.glob("silero*.bin")):
        return path
    raise RuntimeError("VAD model not downloaded")


def snapshot_status() -> dict:
    return dict(_status)


def download_model(root: Path, model_id: str) -> None:
    entry = next((m for m in load_registry(root) if m["id"] == model_id), None)
    if entry is None:
        raise ValueError(f"unknown model: {model_id}")
    if not _single.acquire(blocking=False):
        raise RuntimeError("another model download is running")
    try:
        target = models_dir(root) / entry["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        _status[model_id] = {"status": "downloading", "received": 0, "total": 0}

        def progress(received: int, total: int) -> None:
            _status[model_id] = {"status": "downloading", "received": received, "total": total}

        try:
            download_file(entry["url"], target, progress)
        except Exception as exc:
            _status[model_id] = {"status": "error", "error": str(exc)[:500]}
            raise RuntimeError(f"model download failed: {exc}") from exc
        _status[model_id] = {
            "status": "done",
            "received": target.stat().st_size,
            "total": target.stat().st_size,
        }
    finally:
        _single.release()
