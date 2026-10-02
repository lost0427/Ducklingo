import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from backend.translation import (
    build_translation_batches,
    context_budget,
    create_archive,
    prompt_hash,
    translate_sentences,
    validate_translation_response,
)


def source_hash(sentences: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(sentences, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class TranslationHelpersTest(unittest.TestCase):
    def test_default_budget_and_ratio_bounds(self):
        self.assertEqual(context_budget({}), 104857)
        self.assertEqual(context_budget({"context_window_tokens": 100, "max_context_ratio": 0.1}), 10)
        with self.assertRaises(ValueError):
            context_budget({"max_context_ratio": 0.09})

    def test_batches_are_contiguous(self):
        sentences = [{"text": "word " * 30} for _ in range(20)]
        batches = build_translation_batches(sentences, "Chinese", config={"context_window_tokens": 150, "max_context_ratio": 0.8})
        self.assertEqual(batches[0]["start"], 0)
        self.assertEqual(batches[-1]["end"], len(sentences))
        self.assertTrue(all(left["end"] == right["start"] for left, right in zip(batches, batches[1:])))

    def test_response_requires_exact_nonempty_ids(self):
        self.assertEqual(validate_translation_response('{"1":"你好"}', ["1"]), {"1": "你好"})
        with self.assertRaises(RuntimeError):
            validate_translation_response('{"1":""}', ["1"])


class TranslationArchiveTest(unittest.TestCase):
    def test_empty_archive_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "empty subtitles"):
                create_archive(Path(directory), [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_archive_has_four_timestamped_srt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = create_archive(Path(directory), [{"text": "hello", "translation": "你好", "start": 0.0, "end": 1.0}])
            with ZipFile(path) as archive:
                names = sorted(archive.namelist())
            self.assertEqual(len(names), 4)
            self.assertTrue(all(name.endswith(".srt") for name in names))
            self.assertTrue(path.name.endswith("_subtitles.zip"))


class TranslationCheckpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_translation_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "empty subtitles"):
                await translate_sentences(lambda _: None, [], {}, {}, Path(directory) / "t.json")

    async def test_checkpoint_and_reasoning_effort(self):
        captured = {}

        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": '{"1":"你好"}'}}]}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                captured.update(kwargs["json"])
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "translation.json"
            with patch("backend.translation.httpx.AsyncClient", return_value=Client()):
                result = await translate_sentences(
                    lambda stage: None,
                    [{"text": "hello", "start": 0.0, "end": 1.0}],
                    {"base_url": "https://llm/v1", "api_key": "k", "model": "m"},
                    {"batch_retries": 0},
                    checkpoint,
                    target_language="简体中文",
                    reasoning_effort="high",
                )
            self.assertEqual(result[0]["translation"], "你好")
            self.assertEqual(captured["reasoning_effort"], "high")
            self.assertEqual(json.loads(checkpoint.read_text("utf-8"))["translations"], {"1": "你好"})

    async def test_soft_config_keeps_translated_ids(self):
        calls = []

        class Response:
            status_code = 200
            text = ""

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False

            async def post(self, url, **kwargs):
                items = json.loads(kwargs["json"]["messages"][1]["content"])["items"]
                calls.append([item["id"] for item in items])
                return Response(json.dumps({item["id"]: "new-" + item["id"] for item in items}))

        sentences = [{"text": "one", "start": 0.0, "end": 1.0}, {"text": "two", "start": 1.0, "end": 2.0}]
        with tempfile.TemporaryDirectory() as directory, patch("backend.translation.httpx.AsyncClient", return_value=Client()):
            checkpoint = Path(directory) / "translation.json"
            checkpoint.write_text(json.dumps({
                "source_hash": source_hash(sentences),
                "target_language": "简体中文",
                "mode": "standard",
                "prompt_hash": prompt_hash("standard"),
                "translations": {"1": "old-1"},
            }), "utf-8")
            result = await translate_sentences(
                lambda stage: None, sentences,
                {"base_url": "https://llm/v1", "api_key": "k", "model": "new"},
                {"batch_retries": 0}, checkpoint,
            )
        self.assertEqual([item["translation"] for item in result], ["old-1", "new-2"])
        self.assertEqual(calls, [["2"]])

    async def test_invalid_batch_is_retried_then_bisected(self):
        calls = []

        class Response:
            status_code = 200
            text = ""

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, url, **kwargs):
                items = json.loads(kwargs["json"]["messages"][1]["content"])["items"]
                calls.append(len(items))
                return Response("{}" if len(items) > 1 else json.dumps({items[0]["id"]: items[0]["source"]}))

        sentences = [{"text": "one", "start": 0.0, "end": 1.0}, {"text": "two", "start": 1.0, "end": 2.0}]
        with tempfile.TemporaryDirectory() as directory, patch("backend.translation.httpx.AsyncClient", return_value=Client()):
            result = await translate_sentences(
                lambda stage: None, sentences,
                {"base_url": "https://llm/v1", "api_key": "k", "model": "m"},
                {"batch_retries": 1}, Path(directory) / "translation.json",
            )
        self.assertEqual([item["translation"] for item in result], ["one", "two"])
        self.assertEqual(calls, [2, 2, 1, 1])


if __name__ == "__main__":
    unittest.main()
