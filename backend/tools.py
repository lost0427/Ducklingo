import json
import platform
import re
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path


GITHUB_API = "https://api.github.com"
YTDLP_RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response:
        temp = target.with_suffix(target.suffix + ".part")
        with temp.open("wb") as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
        if temp.stat().st_size == 0:
            raise OSError(f"empty download: {url}")
        temp.replace(target)


def _platform() -> str:
    system = platform.system().lower()
    if system not in {"windows", "linux"} or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Ducklingo supports Windows/Linux x86_64 tools only")
    return system


def ensure_ytdlp(root: Path) -> Path:
    system = _platform()
    asset = "yt-dlp.exe" if system == "windows" else "yt-dlp"
    target = root / asset
    root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        result = subprocess.run([str(target), "-U"], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "yt-dlp update failed")
    else:
        _download(f"{YTDLP_RELEASE}/{asset}", target)
    if not target.exists():
        raise FileNotFoundError(target)
    if system == "linux":
        target.chmod(target.stat().st_mode | 0o111)
    return target


def _ffmpeg_asset(tag: str, variant: str) -> tuple[str, str]:
    system = _platform()
    suffix = "zip" if system == "windows" else "tar.xz"
    prefix = "win64" if system == "windows" else "linux64"
    response = urllib.request.urlopen(f"{GITHUB_API}/repos/BtbN/FFmpeg-Builds/releases/tags/{tag}", timeout=30)
    assets = json.load(response)["assets"]
    pattern = re.compile(rf"ffmpeg-.*-{prefix}-{variant}\.{suffix}$")
    for asset in assets:
        if pattern.fullmatch(asset["name"]):
            return asset["name"], asset["browser_download_url"]
    raise RuntimeError(f"FFmpeg asset not found for {system} x86_64: {tag}")


def ensure_ffmpeg(root: Path, tag: str, variant: str = "gpl") -> Path:
    name, url = _ffmpeg_asset(tag, variant)
    install = root / "ffmpeg" / tag
    binary = next(install.rglob("ffmpeg.exe"), None) if install.exists() else None
    binary = binary or (next(install.rglob("ffmpeg"), None) if install.exists() else None)
    if binary:
        return binary
    archive = root / "ffmpeg" / name
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        _download(url, archive)
    install.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as file:
            file.extractall(install)
    else:
        with tarfile.open(archive, "r:xz") as file:
            file.extractall(install)
    binary = next(install.rglob("ffmpeg.exe"), None) or next(install.rglob("ffmpeg"), None)
    if not binary:
        raise FileNotFoundError(f"ffmpeg not found after extracting {archive}")
    if binary.name == "ffmpeg":
        binary.chmod(binary.stat().st_mode | 0o111)
    return binary
