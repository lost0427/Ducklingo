import json
import subprocess
from pathlib import Path

from .config import load_config, project_path
from .tools import ensure_ffmpeg, ensure_untracker, ensure_ytdlp
from .video import VideoQuality, download_video


def untrack_url(url: str, root: Path | None = None) -> str:
    # Best-effort display cleaning: missing config, missing binary, nonzero
    # exit, timeout or parse failure all fall back to the raw URL so a
    # finished download is never broken. Network errors inside untracker
    # already return the URL unchanged.
    root = root or Path.cwd()
    try:
        config = load_config(project_path("config/config.toml", root))
        binary = ensure_untracker(project_path(config["tools"]["data_dir"], root))
        result = subprocess.run(
            [str(binary), "--json", "--timeout", "5", url],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return str(json.loads(result.stdout)["output"])
    except Exception:
        return url


def download_from_config(
    url: str,
    output: str | Path,
    config_path: str | Path = "config/config.toml",
    root: Path | None = None,
    quality: VideoQuality = "best",
) -> Path:
    root = root or Path.cwd()
    config = load_config(project_path(str(config_path), root))
    tools = project_path(config["tools"]["data_dir"], root)
    ytdlp = ensure_ytdlp(tools)
    ffmpeg = ensure_ffmpeg(
        tools,
        config["tools"]["ffmpeg_release_tag"],
        config["tools"].get("ffmpeg_variant", "gpl"),
    )
    download = config.get("download", {})
    return download_video(
        url,
        project_path(str(output), root),
        ytdlp,
        ffmpeg,
        download.get("retries", 3),
        download.get("retry_delay_seconds", 2),
        quality,
        download.get("timeout_seconds", 3600),
    )
