import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.models import (
    download_model,
    list_models,
    load_registry,
    resolve_model,
    resolve_vad,
    snapshot_status,
)
from backend.asr_worker import read_pcm, words_from_segments
from backend.asr import checkpoint_words


REGISTRY = """
[[models]]
id = "ggml-base-en"
kind = "asr"
name = "Whisper base.en"
url = "https://example.com/ggml-base.en.bin"
filename = "ggml-base.en.bin"

[[models]]
id = "silero-vad"
kind = "vad"
name = "Silero VAD"
url = "https://example.com/silero-v6.2.0.bin"
filename = "silero-v6.2.0.bin"
"""


def root_with(directory: str, files: dict[str, bytes] | None = None) -> Path:
    root = Path(directory)
    (root / "config").mkdir()
    (root / "config" / "asr_models.toml").write_text(REGISTRY, encoding="utf-8")
    models = root / "data" / "models" / "asr"
    models.mkdir(parents=True, exist_ok=True)
    for name, content in (files or {}).items():
        (models / name).write_bytes(content)
    return root


class RegistryTest(unittest.TestCase):
    def test_load_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            entries = load_registry(root_with(directory))
            self.assertEqual([e["id"] for e in entries], ["ggml-base-en", "silero-vad"])


class ListModelsTest(unittest.TestCase):
    def test_merges_manual_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory, {"ggml-base.en.bin": b"m", "custom.gguf": b"c"})
            models = {m["id"]: m for m in list_models(root)}
            self.assertTrue(models["ggml-base-en"]["installed"])
            self.assertFalse(models["ggml-base-en"]["manual"])
            self.assertEqual(models["custom"]["kind"], "asr")
            self.assertTrue(models["custom"]["manual"])
            self.assertFalse(models["silero-vad"]["installed"])


class ResolveTest(unittest.TestCase):
    def test_registry_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory, {"ggml-base.en.bin": b"m"})
            self.assertTrue(resolve_model(root, "ggml-base-en").exists())

    def test_registry_model_missing_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                resolve_model(root_with(directory), "ggml-base-en")

    def test_manual_file_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory, {"custom.gguf": b"c"})
            self.assertTrue(resolve_model(root, "custom").exists())
            with self.assertRaises(RuntimeError):
                resolve_model(root, "absent")

    def test_vad_from_registry_then_glob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory)
            with self.assertRaises(RuntimeError):
                resolve_vad(root)
            (root / "data" / "models" / "asr" / "silero-v6.2.0.bin").write_bytes(b"v")
            self.assertTrue(resolve_vad(root).name.startswith("silero"))


class DownloadModelTest(unittest.TestCase):
    def test_download_records_progress_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory)

            def fake_download(url, target, on_progress=None):
                on_progress(5, 10)
                target.write_bytes(b"model-bytes")

            with patch("backend.models.download_file", fake_download):
                download_model(root, "ggml-base-en")
            status = snapshot_status()["ggml-base-en"]
            self.assertEqual(status["status"], "done")
            self.assertTrue(resolve_model(root, "ggml-base-en").exists())

    def test_download_failure_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = root_with(directory)

            def fake_download(url, target, on_progress=None):
                raise OSError("network down")

            with patch("backend.models.download_file", fake_download):
                with self.assertRaises(RuntimeError):
                    download_model(root, "ggml-base-en")
            self.assertEqual(snapshot_status()["ggml-base-en"]["status"], "error")

    def test_unknown_model_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download_model(root_with(directory), "nope")


class CheckpointWordsTest(unittest.TestCase):
    def test_cloud_and_local_shapes(self):
        cloud = {"chunks": [{"words": [{"text": "a", "start": 0.0, "end": 1.0}]}]}
        local = {"words": [{"text": "b", "start": 0.0, "end": 1.0}]}
        self.assertEqual(len(checkpoint_words(cloud)), 1)
        self.assertEqual(checkpoint_words(local)[0]["text"], "b")


class WorkerHelpersTest(unittest.TestCase):
    def test_read_pcm_memmaps_float32_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.pcm"
            path.write_bytes(struct.pack("<2f", 1.0, -0.5))
            pcm = read_pcm(path)
            self.assertIsInstance(pcm, np.memmap)
            self.assertAlmostEqual(float(pcm[0]), 1.0)
            self.assertAlmostEqual(float(pcm[1]), -0.5)
            pcm._mmap.close()  # release the Windows file lock before cleanup

    def test_words_from_segments(self):
        segments = [
            SimpleNamespace(words=[
                SimpleNamespace(text=" hi ", start=0.0, end=0.5),
                SimpleNamespace(text="", start=0.5, end=0.6),
            ]),
            SimpleNamespace(words=None),
        ]
        self.assertEqual(
            words_from_segments(segments),
            [{"text": "hi", "start": 0.0, "end": 0.5}],
        )


if __name__ == "__main__":
    unittest.main()
