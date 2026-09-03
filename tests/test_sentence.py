import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.sentence import apply_breaks, batch_words, call_llm, join_text, split_sentences, system_prompt, validate_lengths

WORDS = [
    {"text": "the", "start": 0.0, "end": 0.1},
    {"text": "quick", "start": 0.1, "end": 0.2},
    {"text": "brown", "start": 0.2, "end": 0.3},
    {"text": "fox", "start": 0.3, "end": 0.4},
    {"text": "runs", "start": 0.4, "end": 0.5},
    {"text": "fast", "start": 0.5, "end": 0.6},
]


class SystemPromptTest(unittest.TestCase):
    def test_contains_both_limits(self):
        prompt = system_prompt({"max_chars_cjk": 21, "max_chars_latin": 42})
        self.assertIn("CJK languages (Chinese, Japanese, Korean): 21", prompt)
        self.assertIn("All other languages", prompt)


class BatchWordsTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual([len(b) for b in batch_words(WORDS, 4)], [4, 2])
        self.assertEqual(batch_words([], 4), [])


class JoinTextTest(unittest.TestCase):
    def test_collapses_cjk_spaces_only(self):
        self.assertEqual(join_text([{"text": "你"}, {"text": "好"}]), "你好")
        self.assertEqual(join_text(WORDS[:2]), "the quick")


class ApplyBreaksTest(unittest.TestCase):
    def test_splits_and_times(self):
        sentences = apply_breaks(WORDS, "the quick brown fox<br>runs fast")
        self.assertEqual(sentences, [
            {"text": "the quick brown fox", "start": 0.0, "end": 0.4},
            {"text": "runs fast", "start": 0.4, "end": 0.6},
        ])

    def test_no_breaks(self):
        self.assertEqual(apply_breaks(WORDS[:2], "the quick"), [
            {"text": "the quick", "start": 0.0, "end": 0.2},
        ])

    def test_dropped_word_raises(self):
        with self.assertRaises(ValueError):
            apply_breaks(WORDS, "the quick fox<br>runs fast")

    def test_changed_word_raises(self):
        with self.assertRaises(ValueError):
            apply_breaks(WORDS, "the quick brown fox<br>runs slowly")

    def test_extra_word_raises(self):
        with self.assertRaises(ValueError):
            apply_breaks(WORDS[:2], "the quick brown")

    def test_duplicate_break_tolerated(self):
        sentences = apply_breaks(WORDS[:4], "the quick<br><br>brown fox")
        self.assertEqual(len(sentences), 2)


class ValidateLengthsTest(unittest.TestCase):
    def test_latin_limit_ignores_spaces(self):
        sentence = {"text": "a " * 42, "start": 0.0, "end": 1.0}
        self.assertEqual(validate_lengths([sentence], {"max_chars_latin": 42}), [sentence])

    def test_latin_over_limit_raises(self):
        with self.assertRaisesRegex(ValueError, r"sentence too long: 43>42"):
            validate_lengths([{"text": "a" * 43}], {"max_chars_latin": 42})

    def test_cjk_limit(self):
        self.assertEqual(
            validate_lengths([{"text": "你" * 21}], {"max_chars_cjk": 21}),
            [{"text": "你" * 21}],
        )
        with self.assertRaisesRegex(ValueError, r"sentence too long: 22>21"):
            validate_lengths([{"text": "你" * 22}], {"max_chars_cjk": 21})


class CallLlmTest(unittest.TestCase):
    def run_transport(self, handler, content_text="a<br>b"):
        captured = {}

        def handle(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return handler(request)

        transport = httpx.MockTransport(handle)
        settings = {"base_url": "https://llm.example/v1", "api_key": "k", "model": "m"}
        config = {"retries": 1, "max_chars_cjk": 21, "max_chars_latin": 42}

        async def main():
            async with httpx.AsyncClient(transport=transport) as client:
                return await call_llm(client, "a b", "PROMPT", settings, config)

        content = asyncio.run(main())
        self.assertEqual(captured["body"]["temperature"], 0.1)
        self.assertEqual(captured["body"]["messages"][0]["content"], "PROMPT")
        self.assertEqual(captured["body"]["messages"][1]["content"], "<text>a b</text>")
        self.assertEqual(content, content_text)
        return captured

    def test_plain_content(self):
        def handle(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "a<br>b"}}]})

        self.run_transport(handle)

    def test_response_format_wrapper(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"text": "a<br>b"}'}}]})

        config_extra = {"response_format": "json_object"}

        async def main():
            transport = httpx.MockTransport(handle)
            settings = {"base_url": "https://llm.example/v1", "api_key": "k", "model": "m"}
            config = {"retries": 0, **config_extra}
            async with httpx.AsyncClient(transport=transport) as client:
                return await call_llm(client, "a b", "P", settings, config)

        self.assertEqual(asyncio.run(main()), "a<br>b")

    def test_retries_on_500(self):
        calls = []

        def handle(request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"choices": [{"message": {"content": "a<br>b"}}]})

        self.run_transport(handle)
        self.assertEqual(len(calls), 2)

    def test_4xx_not_retried(self):
        calls = []

        def handle(request):
            calls.append(1)
            return httpx.Response(401, text="bad key")

        with tempfile.TemporaryDirectory() as directory:
            async def main():
                transport = httpx.MockTransport(handle)
                settings = {"base_url": "https://llm.example", "api_key": "k", "model": "m"}
                async with httpx.AsyncClient(transport=transport) as client:
                    await call_llm(client, "a b", "P", settings, {"retries": 3})

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(main())
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_missing_choices_raises(self):
        def handle(request):
            return httpx.Response(200, json={"nope": 1})

        async def main():
            transport = httpx.MockTransport(handle)
            settings = {"base_url": "https://llm.example", "api_key": "k", "model": "m"}
            async with httpx.AsyncClient(transport=transport) as client:
                await call_llm(client, "a b", "P", settings, {"retries": 0})

        with self.assertRaises(RuntimeError):
            asyncio.run(main())


class SplitSentencesTest(unittest.TestCase):
    def test_resume_skips_done_batches(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                checkpoint = Path(directory) / "s.json"
                checkpoint.write_text(json.dumps({
                    "word_count": 6,
                    "batches": [
                        {"done": True, "sentences": [{"text": "the quick", "start": 0.0, "end": 0.2}]},
                        {"done": False, "sentences": []},
                    ],
                }), "utf-8")
                calls = []

                async def fake_call(client, text, prompt, settings, config):
                    calls.append(text)
                    return "runs fast"

                config = {"batch_words": 4, "max_chars_cjk": 21, "max_chars_latin": 42}
                with patch("backend.sentence.call_llm", fake_call):
                    sentences = await split_sentences(lambda s: None, WORDS, {}, config, checkpoint)
                self.assertEqual(calls, ["runs fast"])
                self.assertEqual(len(sentences), 2)
                self.assertEqual(sentences[0]["text"], "the quick")

        asyncio.run(main())

    def test_word_mismatch_retries_then_errors(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                checkpoint = Path(directory) / "s.json"
                attempts = []

                async def fake_call(client, text, prompt, settings, config):
                    attempts.append(1)
                    return "the quick brown fox runs fastly"

                config = {"batch_words": 800}
                with patch("backend.sentence.call_llm", fake_call):
                    with self.assertRaises(RuntimeError) as ctx:
                        await split_sentences(lambda s: None, WORDS, {}, config, checkpoint)
                self.assertEqual(len(attempts), 2)
                self.assertIn("word sequence mismatch", str(ctx.exception))

        asyncio.run(main())

    def test_length_mismatch_retries_then_errors(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                checkpoint = Path(directory) / "s.json"
                attempts = []

                async def fake_call(client, text, prompt, settings, config):
                    attempts.append(1)
                    return "a" * 43

                with patch("backend.sentence.call_llm", fake_call):
                    with self.assertRaisesRegex(RuntimeError, r"sentence too long: 43>42"):
                        await split_sentences(
                            lambda s: None,
                            [{"text": "a" * 43, "start": 0.0, "end": 1.0}],
                            {},
                            {"max_chars_latin": 42},
                            checkpoint,
                        )
                self.assertEqual(len(attempts), 2)

        asyncio.run(main())

    def test_transport_error_reports_batch_number(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                checkpoint = Path(directory) / "s.json"

                async def fake_call(client, text, prompt, settings, config):
                    raise RuntimeError("LLM call failed: HTTP 429")

                config = {"batch_words": 4}
                with patch("backend.sentence.call_llm", fake_call):
                    with self.assertRaises(RuntimeError) as ctx:
                        await split_sentences(lambda s: None, WORDS, {}, config, checkpoint)
                self.assertIn("sentence batch 1/2 failed: LLM call failed: HTTP 429", str(ctx.exception))

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
