import asyncio
import json
from pathlib import Path

from .asr import load_checkpoint, probe_duration, save_checkpoint, transcribe_cloud, transcribe_local, validate_timeline
from .config import load_config, project_path
from .sentence import split_sentences
from .tools import ensure_ffmpeg
from .translation import create_archive, translate_sentences


def load_section(root: Path, name: str) -> dict:
    path = Path(root) / "config" / "config.toml"
    return load_config(path).get(name, {}) if path.exists() else {}


def resolve_ffmpeg(root: Path) -> Path:
    tools = load_section(root, "tools")
    return ensure_ffmpeg(
        project_path(tools.get("data_dir", "data/tools"), root),
        tools.get("ffmpeg_release_tag", "autobuild-2026-08-25-13-06"),
        tools.get("ffmpeg_variant", "gpl"),
    )


def load_sentences(directory: Path, file_id: str) -> list[dict]:
    state = load_checkpoint(directory / f"{file_id}.sentences.json") or {}
    return [sentence for batch in state.get("batches", []) for sentence in batch["sentences"]]


async def run_asr(report, root: Path, source: Path, directory: Path, file_id: str, settings: dict, section: dict) -> list[dict]:
    ffmpeg = await asyncio.to_thread(resolve_ffmpeg, root)
    if settings["kind"] == "cloud":
        return await transcribe_cloud(report, source, directory, file_id, settings, section, ffmpeg)
    return await transcribe_local(report, source, directory, file_id, settings, section, ffmpeg, root)


async def run_sentence(report, directory: Path, file_id: str, words: list[dict], settings: dict, section: dict, timeout: float) -> list[dict]:
    return await split_sentences(report, words, settings, section, directory / f"{file_id}.sentences.json", timeout)


async def run_translate(
    report,
    root: Path,
    source: Path,
    directory: Path,
    file_id: str,
    user_id: int,
    sentences: list[dict],
    settings: dict,
    section: dict,
    target_language: str,
    mode: str,
    reasoning_effort: str,
    timeout: float,
) -> Path:
    ffmpeg = await asyncio.to_thread(resolve_ffmpeg, root)
    duration = await asyncio.to_thread(probe_duration, source, ffmpeg)
    sentences = validate_timeline(sentences, duration)
    translated = await translate_sentences(
        report,
        sentences,
        settings,
        section,
        directory / f"{file_id}.translation.json",
        target_language=target_language,
        mode=mode,
        reasoning_effort=reasoning_effort,
        cache_dir=Path(root) / "data" / "translation-cache" / str(user_id),
        timeout=timeout,
    )
    archive = await asyncio.to_thread(create_archive, directory, translated)
    state = json.loads((directory / f"{file_id}.translation.json").read_text("utf-8"))
    state["archive"] = archive.name
    save_checkpoint(directory / f"{file_id}.translation.json", state)
    return archive


async def run_pipeline(
    report,
    root: Path,
    source: Path,
    directory: Path,
    file_id: str,
    user_id: int,
    *,
    asr_settings: dict,
    asr_section: dict,
    sentence_settings: dict,
    sentence_section: dict,
    translate_settings: dict,
    translate_section: dict,
    target_language: str,
    mode: str,
    reasoning_effort: str,
    has_target: bool,
) -> None:
    timeout = float(asr_section.get("timeout_seconds", 300))
    words = await run_asr(report, root, source, directory, file_id, asr_settings, asr_section)
    sentences = await run_sentence(report, directory, file_id, words, sentence_settings, sentence_section, timeout)
    if has_target:
        await run_translate(
            report,
            root,
            source,
            directory,
            file_id,
            user_id,
            sentences,
            translate_settings,
            translate_section,
            target_language,
            mode,
            reasoning_effort,
            float(translate_section.get("timeout_seconds", timeout)),
        )
