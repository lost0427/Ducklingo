import argparse
import inspect
import json
from pathlib import Path

import numpy as np


def read_pcm(path: Path) -> np.memmap:
    # Whether the binding copies this array into its own buffer is unverified:
    # if it does, RAM returns to full size and this change is merely harmless.
    # Check RSS in the real-model smoke test. Fallback if ever needed:
    # silence-aligned outer chunking via plan_chunks + offset merge.
    return np.memmap(path, dtype=np.float32, mode="r")


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
