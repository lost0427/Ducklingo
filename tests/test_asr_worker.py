import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.asr_worker import main


class AlignerCallTest(unittest.TestCase):
    def test_aligner_uses_crispasr_argument_order(self):
        self.check_aligner([])

    def test_zero_duration_words_trigger_aligner(self):
        self.check_aligner([types.SimpleNamespace(text="hello", start=0.23, end=0.23)])

    def test_zero_duration_alignment_fails(self):
        with self.assertRaisesRegex(SystemExit, "no valid word timestamps"):
            self.check_aligner([], aligned_end=0)

    def test_valid_words_skip_aligner(self):
        self.check_aligner([
            types.SimpleNamespace(text="hello", start=0, end=1),
            types.SimpleNamespace(text="!", start=1, end=1),
        ], expect_aligner=False)

    def check_aligner(self, words, aligned_end=1, expect_aligner=True):
        calls = []

        class Session:
            def __init__(self, model):
                pass

            def transcribe_vad(self, pcm, vad):
                return [types.SimpleNamespace(text="hello", words=words)]

        def align_words(model, transcript, pcm):
            calls.append((model, transcript, pcm.tolist()))
            return [types.SimpleNamespace(text="hello", start=0, end=aligned_end)]

        fake = types.SimpleNamespace(Session=Session, align_words=align_words)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcm = root / "audio.pcm"
            output = root / "words.json"
            np.array([0.0], dtype=np.float32).tofile(pcm)
            argv = ["asr_worker", "--model", "asr.bin", "--pcm", str(pcm), "--vad", "vad.bin",
                    "--aligner", "aligner.bin", "--output", str(output)]
            with patch.dict(sys.modules, {"crispasr": fake}), patch.object(sys, "argv", argv):
                main()

        self.assertEqual(calls, [("aligner.bin", "hello", [0.0])] if expect_aligner else [])


if __name__ == "__main__":
    unittest.main()
