import subprocess
import time
from pathlib import Path


def download_video(
    url: str,
    output: str | Path,
    ytdlp: Path,
    ffmpeg: Path,
    retries: int = 3,
    retry_delay: float = 2,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ytdlp),
        "--no-playlist",
        "--continue",
        "--ffmpeg-location",
        str(ffmpeg.parent),
        "--output",
        str(output),
        url,
    ]
    for attempt in range(retries + 1):
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0 and output.exists():
            return output
        if attempt == retries:
            raise RuntimeError(result.stderr.strip() or "video download failed")
        time.sleep(retry_delay * (attempt + 1))
