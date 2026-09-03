import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import httpx

from .models import resolve_model, resolve_vad

SILENCE_RE = re.compile(r"silence_start: ([0-9.]+)")
BITRATE_BYTES_PER_SECOND = 4000  # 32 kbps mp3


def format_ts(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    return "\n".join(
        f"{i}\n{format_ts(s['start'])} --> {format_ts(s['end'])}\n{s['text'].strip()}\n"
        for i, s in enumerate(segments, 1)
    )


def validate_timeline(segments: list[dict], duration: float | None = None) -> list[dict]:
    clean: list[dict] = []
    for seg in segments:
        start = max(0.0, float(seg["start"]))
        end = float(seg["end"])
        if duration is not None:
            start = min(start, duration)
            end = min(end, duration)
        if clean and start < clean[-1]["end"]:
            start = clean[-1]["end"]
        if end - start < 0.01:
            continue
        clean.append({**seg, "start": start, "end": end})
    return clean


def endpoint(settings: dict) -> str:
    base = settings["base_url"].rstrip("/")
    if settings.get("api_flavor") == "mistral":
        return f"{base}/v1/audio/transcriptions"
    return f"{base}/audio/transcriptions"


def headers(settings: dict) -> dict:
    if settings.get("auth_style") == "x-api-key":
        return {"x-api-key": settings["api_key"]}
    return {"Authorization": f"Bearer {settings['api_key']}"}


def parse_words(data: dict) -> list[dict]:
    words = data.get("words")
    if not words:
        raise RuntimeError("transcription response has no word timestamps")
    return [
        {
            "text": str(w.get("word") or w.get("text") or "").strip(),
            "start": float(w["start"]),
            "end": float(w["end"]),
        }
        for w in words
    ]


def offset_words(words: list[dict], offset: float) -> list[dict]:
    return [
        {**w, "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)}
        for w in words
    ]


def needs_chunking(duration: float, max_mb: float) -> bool:
    return duration * BITRATE_BYTES_PER_SECOND > max_mb * 1024 * 1024


def parse_silences(stderr: str) -> list[float]:
    return [float(v) for v in SILENCE_RE.findall(stderr)]


def plan_chunks(
    duration: float,
    silences: list[float],
    target: float = 600.0,
    tolerance: float = 90.0,
    min_len: float = 120.0,
) -> list[tuple[float, float]]:
    boundaries = [0.0]
    while duration - boundaries[-1] > target + tolerance:
        goal = boundaries[-1] + target
        low = max(boundaries[-1] + min_len, goal - tolerance)
        candidates = [s for s in silences if low < s <= goal + tolerance]
        cut = min(candidates, key=lambda s: abs(s - goal)) if candidates else goal
        boundaries.append(cut)
    boundaries.append(duration)
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if b - a > 0.5]


def _run_ffmpeg(command: list[str], timeout: float, what: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{what} failed: {result.stderr.strip()[-500:]}")
    return result.stderr


def ffprobe_path(ffmpeg: Path) -> Path:
    return ffmpeg.with_name("ffprobe" + ffmpeg.suffix)


def extract_audio(source: Path, target: Path, ffmpeg: Path, fmt: str, timeout: float) -> Path:
    command = [str(ffmpeg), "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000"]
    if fmt == "mp3":
        command += ["-c:a", "libmp3lame", "-b:a", "32k"]
    else:
        command += ["-f", "f32le", "-c:a", "pcm_f32le"]
    command.append(str(target))
    _run_ffmpeg(command, timeout, "audio extraction")
    return target


def probe_duration(path: Path, ffmpeg: Path) -> float:
    result = subprocess.run(
        [str(ffprobe_path(ffmpeg)), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return float(result.stdout.strip())


def detect_silences(path: Path, ffmpeg: Path, timeout: float) -> list[float]:
    stderr = _run_ffmpeg(
        [str(ffmpeg), "-i", str(path), "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
        timeout,
        "silence detection",
    )
    return parse_silences(stderr)


def split_audio(mp3: Path, chunks: list[dict], ffmpeg: Path, workdir: Path, file_id: str, timeout: float) -> list[Path]:
    paths = []
    for i, chunk in enumerate(chunks):
        target = workdir / f"{file_id}.asr-{i}.mp3"
        if not target.exists():
            _run_ffmpeg(
                [str(ffmpeg), "-y", "-ss", f"{chunk['start']:.3f}", "-t", f"{chunk['end'] - chunk['start']:.3f}",
                 "-i", str(mp3), "-c:a", "libmp3lame", "-b:a", "32k", str(target)],
                timeout,
                "audio split",
            )
        paths.append(target)
    return paths


def load_checkpoint(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def save_checkpoint(path: Path, state: dict) -> None:
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(state), "utf-8")
    temp.replace(path)


async def transcribe_chunk(path: Path, settings: dict, asr: dict) -> list[dict]:
    retries = int(asr.get("retries", 2))
    async with httpx.AsyncClient(timeout=float(asr.get("timeout_seconds", 300))) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.post(
                    endpoint(settings),
                    headers=headers(settings),
                    data={"model": settings["model"], "timestamp_granularities": "word"},
                    files={"file": (path.name, path.read_bytes(), "audio/mpeg")},
                )
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise RuntimeError(f"transcription failed: {exc}") from exc
            else:
                if response.status_code < 400:
                    try:
                        return parse_words(response.json())
                    except ValueError as exc:
                        raise RuntimeError("transcription returned invalid JSON") from exc
                if (response.status_code < 500 and response.status_code != 429) or attempt == retries:
                    raise RuntimeError(
                        f"transcription failed: HTTP {response.status_code}: {response.text[:200]}"
                    )
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def checkpoint_words(state: dict) -> list[dict]:
    if "words" in state:
        return state["words"]
    return [word for chunk in state.get("chunks", []) for word in chunk["words"]]


_LOCAL_WORKERS: dict[str, subprocess.Popen] = {}


def kill_local(file_id: str) -> None:
    process = _LOCAL_WORKERS.get(file_id)
    if process and process.poll() is None:
        process.terminate()


def _run_local_worker(
    command: list[str], cwd: Path, env: dict | None, timeout: float, file_id: str,
    cancelled: threading.Event | None = None,
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd),
        env=env,
    )
    _LOCAL_WORKERS[file_id] = process
    if cancelled and cancelled.is_set():
        process.terminate()
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("local transcription timed out") from None
    finally:
        _LOCAL_WORKERS.pop(file_id, None)
    if process.returncode:
        raise RuntimeError(f"local transcription failed: {stderr.strip()[-500:]}")


async def transcribe_local(
    report,
    source: Path,
    workdir: Path,
    file_id: str,
    settings: dict,
    asr: dict,
    ffmpeg: Path,
    root: Path,
) -> list[dict]:
    timeout = float(asr.get("local_timeout_seconds", 3600))
    report("extract")
    pcm = await asyncio.to_thread(
        extract_audio, source, workdir / f"{file_id}.asr.pcm", ffmpeg, "pcm", timeout
    )
    model = await asyncio.to_thread(resolve_model, root, settings["model"])
    vad = await asyncio.to_thread(resolve_vad, root)
    aligner = (
        await asyncio.to_thread(resolve_model, root, settings["aligner"], "aligner")
        if settings.get("aligner")
        else None
    )
    output = workdir / f"{file_id}.asr.words.json"
    command = [
        sys.executable, "-m", "backend.asr_worker",
        "--model", str(model), "--pcm", str(pcm), "--vad", str(vad), "--output", str(output),
    ]
    if aligner:
        command += ["--aligner", str(aligner)]
    if settings.get("language"):
        command += ["--language", settings["language"]]
    report("transcribe")
    cancelled = threading.Event()
    try:
        await asyncio.to_thread(
            _run_local_worker, command, root, {**os.environ, "PYTHONPATH": str(root)}, timeout, file_id, cancelled
        )
    except asyncio.CancelledError:
        cancelled.set()
        kill_local(file_id)
        raise
    words = json.loads(output.read_text("utf-8"))["words"]
    if not words:
        raise RuntimeError("local transcription produced no word timestamps")
    save_checkpoint(workdir / f"{file_id}.asr.json", {"kind": "local", "words": words})
    return words


async def transcribe_cloud(
    report,
    source: Path,
    workdir: Path,
    file_id: str,
    settings: dict,
    asr: dict,
    ffmpeg: Path,
) -> list[dict]:
    timeout = float(asr.get("timeout_seconds", 300))
    report("extract")
    mp3 = await asyncio.to_thread(
        extract_audio, source, workdir / f"{file_id}.asr.mp3", ffmpeg, "mp3", timeout
    )
    duration = await asyncio.to_thread(probe_duration, mp3, ffmpeg)
    ckpt_path = workdir / f"{file_id}.asr.json"
    state = load_checkpoint(ckpt_path)
    if not state or abs(state.get("duration", -1) - duration) > 0.5:
        if needs_chunking(duration, float(asr.get("max_chunk_mb", 24))):
            silences = await asyncio.to_thread(detect_silences, mp3, ffmpeg, timeout)
            spans = plan_chunks(duration, silences, float(asr.get("chunk_seconds", 600)))
        else:
            spans = [(0.0, duration)]
        state = {"duration": duration, "chunks": [
            {"start": s, "end": e, "done": False, "words": []} for s, e in spans
        ]}
        save_checkpoint(ckpt_path, state)
    report("transcribe")
    paths = await asyncio.to_thread(split_audio, mp3, state["chunks"], ffmpeg, workdir, file_id, timeout)
    for i, chunk in enumerate(state["chunks"]):
        if chunk["done"]:
            continue
        chunk["words"] = offset_words(await transcribe_chunk(paths[i], settings, asr), chunk["start"])
        chunk["done"] = True
        save_checkpoint(ckpt_path, state)
    return [word for chunk in state["chunks"] for word in chunk["words"]]
