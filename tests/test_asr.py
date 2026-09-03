import asyncio
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.asr import (
    _run_local_worker,
    build_srt,
    endpoint,
    format_ts,
    headers,
    kill_local,
    needs_chunking,
    offset_words,
    parse_silences,
    parse_words,
    plan_chunks,
    transcribe_local,
    validate_timeline,
)


class FormatTsTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(format_ts(0), "00:00:00,000")
        self.assertEqual(format_ts(3661.5), "01:01:01,500")
        self.assertEqual(format_ts(-0.5), "00:00:00,000")


class BuildSrtTest(unittest.TestCase):
    def test_blocks(self):
        srt = build_srt([
            {"start": 0.0, "end": 1.5, "text": " hello "},
            {"start": 2.0, "end": 3.0, "text": "world"},
        ])
        self.assertEqual(
            srt,
            "1\n00:00:00,000 --> 00:00:01,500\nhello\n\n2\n00:00:02,000 --> 00:00:03,000\nworld\n",
        )


class ValidateTimelineTest(unittest.TestCase):
    def test_clamps_and_drops(self):
        segments = [
            {"start": -1.0, "end": 2.0, "text": "a"},
            {"start": 1.0, "end": 3.0, "text": "b"},  # overlaps a -> start clamped to 2.0
            {"start": 3.0, "end": 3.0, "text": "c"},  # zero length -> dropped
            {"start": 4.0, "end": 9.0, "text": "d"},  # beyond duration -> clamped
        ]
        clean = validate_timeline(segments, duration=5.0)
        self.assertEqual(
            [(s["start"], s["end"], s["text"]) for s in clean],
            [(0.0, 2.0, "a"), (2.0, 3.0, "b"), (4.0, 5.0, "d")],
        )

    def test_monotonic_without_duration(self):
        clean = validate_timeline([
            {"start": 5.0, "end": 6.0, "text": "a"},
            {"start": 2.0, "end": 8.0, "text": "b"},
        ])
        self.assertEqual(clean[1]["start"], 6.0)


class EndpointTest(unittest.TestCase):
    def test_flavor_paths(self):
        self.assertEqual(
            endpoint({"base_url": "https://api.mistral.ai", "api_flavor": "mistral"}),
            "https://api.mistral.ai/v1/audio/transcriptions",
        )
        self.assertEqual(
            endpoint({"base_url": "https://api.openai.com/v1"}),
            "https://api.openai.com/v1/audio/transcriptions",
        )

    def test_headers(self):
        self.assertEqual(
            headers({"api_key": "k", "auth_style": "x-api-key"}),
            {"x-api-key": "k"},
        )
        self.assertEqual(
            headers({"api_key": "k"}),
            {"Authorization": "Bearer k"},
        )


class ParseWordsTest(unittest.TestCase):
    def test_words_key(self):
        words = parse_words({"words": [{"word": " hi ", "start": 0.1, "end": 0.2}]})
        self.assertEqual(words, [{"text": "hi", "start": 0.1, "end": 0.2}])

    def test_text_fallback(self):
        words = parse_words({"words": [{"text": "hi", "start": "0.1", "end": "0.2"}]})
        self.assertEqual(words[0]["text"], "hi")

    def test_missing_words_raises(self):
        with self.assertRaises(RuntimeError):
            parse_words({"segments": []})


class OffsetWordsTest(unittest.TestCase):
    def test_offsets(self):
        words = offset_words([{"text": "a", "start": 1.0, "end": 2.0}], 600.0)
        self.assertEqual(words, [{"text": "a", "start": 601.0, "end": 602.0}])


class NeedsChunkingTest(unittest.TestCase):
    def test_budget(self):
        self.assertFalse(needs_chunking(100 * 60, 24))  # ~22.9 MiB
        self.assertTrue(needs_chunking(120 * 60, 24))


class ParseSilencesTest(unittest.TestCase):
    def test_parse(self):
        stderr = "[silencedetect @ 0] silence_start: 12.5\nnoise\nsilence_start: 672.1\n"
        self.assertEqual(parse_silences(stderr), [12.5, 672.1])


class PlanChunksTest(unittest.TestCase):
    def test_hard_cut_without_silence(self):
        chunks = plan_chunks(1300.0, [], target=600.0)
        self.assertEqual(chunks, [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1300.0)])

    def test_prefers_silence_near_goal(self):
        chunks = plan_chunks(1300.0, [585.0, 612.0], target=600.0)
        self.assertEqual(chunks[0], (0.0, 612.0))

    def test_silence_beyond_tolerance_ignored(self):
        chunks = plan_chunks(1300.0, [700.0], target=600.0)
        self.assertEqual(chunks[0], (0.0, 600.0))

    def test_min_length_enforced(self):
        # silence at 30s is within tolerance of goal 600 but below min_len
        chunks = plan_chunks(1300.0, [30.0, 640.0], target=600.0)
        self.assertEqual(chunks[0], (0.0, 640.0))

    def test_whole_file_when_short(self):
        self.assertEqual(plan_chunks(500.0, [250.0], target=600.0), [(0.0, 500.0)])


class LocalWorkerTest(unittest.TestCase):
    def test_success(self):
        _run_local_worker([sys.executable, "-c", "print('ok')"], Path("."), None, 30, "t1")

    def test_timeout_kills_process(self):
        with self.assertRaises(RuntimeError):
            _run_local_worker([sys.executable, "-c", "import time; time.sleep(10)"], Path("."), None, 1, "t2")

    def test_nonzero_exit_reports(self):
        with self.assertRaises(RuntimeError):
            _run_local_worker([sys.executable, "-c", "import sys; sys.exit(3)"], Path("."), None, 30, "t3")

    def test_kill_local_terminates(self):
        done = threading.Event()

        def run():
            try:
                _run_local_worker([sys.executable, "-c", "import time; time.sleep(30)"], Path("."), None, 60, "t4")
            except RuntimeError:
                pass
            done.set()

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(1)
        kill_local("t4")
        self.assertTrue(done.wait(10))

    def test_pre_cancelled_worker_terminates(self):
        cancelled = threading.Event()
        cancelled.set()
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            _run_local_worker(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                Path("."), None, 60, "t5", cancelled,
            )
        self.assertLess(time.monotonic() - started, 10)


class LocalTranscriptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_checkpoint_skips_transcription(self):
        words = [{"text": "done", "start": 0.0, "end": 1.0}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cached.asr.json").write_text(
                '{"kind":"local","words":[{"text":"done","start":0.0,"end":1.0}]}', "utf-8"
            )
            with patch("backend.asr.extract_audio") as extract:
                result = await transcribe_local(
                    lambda stage: None, root / "input.mp4", root, "cached",
                    {"model": "model"}, {}, root / "ffmpeg.exe", root,
                )
            extract.assert_not_called()
            self.assertEqual(result, words)

    async def test_cancellation_reaches_worker_thread(self):
        started = threading.Event()
        stopped = threading.Event()

        def worker(command, cwd, env, timeout, file_id, cancelled):
            started.set()
            cancelled.wait(5)
            stopped.set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcm = root / "audio.pcm"
            with (
                patch("backend.asr.extract_audio", return_value=pcm),
                patch("backend.asr.resolve_model", return_value=root / "model.bin"),
                patch("backend.asr.resolve_vad", return_value=root / "vad.bin"),
                patch("backend.asr._run_local_worker", side_effect=worker),
            ):
                task = asyncio.create_task(
                    transcribe_local(lambda stage: None, root / "input.mp4", root, "t6",
                                     {"model": "model"}, {}, root / "ffmpeg.exe", root)
                )
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(await asyncio.to_thread(stopped.wait, 5))


if __name__ == "__main__":
    unittest.main()
