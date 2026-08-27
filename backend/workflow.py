from pathlib import Path

from .config import load_config, project_path
from .tools import ensure_ffmpeg, ensure_ytdlp
from .video import VideoQuality, download_video


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
