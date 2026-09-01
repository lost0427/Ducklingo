import unittest

from backend.asr import (
    build_srt,
    endpoint,
    format_ts,
    headers,
    needs_chunking,
    offset_words,
    parse_silences,
    parse_words,
    plan_chunks,
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


if __name__ == "__main__":
    unittest.main()
