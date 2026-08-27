import subprocess
import time
from pathlib import Path
from typing import Literal, TypeAlias


VideoQuality: TypeAlias = Literal["best", "2160", "1440", "1080", "720", "480", "360"]


def format_selector(quality: str) -> str:
    if quality == "best":
        return "bestvideo+bestaudio/best"
    if quality not in {"2160", "1440", "1080", "720", "480", "360"}:
        raise ValueError("unsupported video quality")
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"


def download_video(
    url: str,
    output: str | Path,
    ytdlp: Path,
    ffmpeg: Path,
    retries: int = 3,
    retry_delay: float = 2,
    quality: VideoQuality = "best",
    timeout_seconds: float = 3600,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ytdlp),
        "--no-playlist",
        "--continue",
        "--ffmpeg-location",
        str(ffmpeg.parent),
        "--format",
        format_selector(quality),
        "--merge-output-format",
        "mp4",
        "--output",
        str(output),
        url,
    ]
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if attempt == retries:
                raise RuntimeError("video download timed out") from exc
        else:
            if result.returncode == 0 and output.exists():
                return output
            if attempt == retries:
                raise RuntimeError(result.stderr.strip() or "video download failed")
        time.sleep(retry_delay * (attempt + 1))
