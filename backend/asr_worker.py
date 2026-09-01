import argparse
import inspect
import json
import wave
from pathlib import Path

import numpy as np


def read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio:
        if audio.getframerate() != 16000 or audio.getnchannels() != 1:
            raise ValueError("pcm must be 16 kHz mono wav")
        data = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def words_from_segments(segments) -> list[dict]:
    words = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            text = str(word.text).strip()
            if text:
                words.append({"text": text, "start": float(word.start), "end": float(word.end)})
    return words


def aligned_words(aligned) -> list[dict]:
    return [
        {"text": str(word.text).strip(), "start": float(word.start), "end": float(word.end)}
        for word in aligned
        if str(word.text).strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pcm", required=True)
    parser.add_argument("--vad", required=True)
    parser.add_argument("--aligner")
    parser.add_argument("--language")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from crispasr import Session, align_words

    pcm = read_pcm(Path(args.pcm))
    session = Session(args.model)
    parameters = inspect.signature(session.transcribe_vad).parameters
    kwargs = {"language": args.language} if args.language and "language" in parameters else {}
    segments = session.transcribe_vad(pcm, args.vad, **kwargs)
    words = words_from_segments(segments)
    if not words and args.aligner:
        text = " ".join(str(segment.text) for segment in segments)
        words = aligned_words(align_words(pcm, text, args.aligner))
    Path(args.output).write_text(json.dumps({"words": words}), "utf-8")
    if not words:
        raise SystemExit("no word timestamps produced")


if __name__ == "__main__":
    main()
