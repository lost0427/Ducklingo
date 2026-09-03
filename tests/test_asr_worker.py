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
        calls = []

        class Session:
            def __init__(self, model):
                pass

            def transcribe_vad(self, pcm, vad):
                return [types.SimpleNamespace(text="hello", words=[])]

        def align_words(model, transcript, pcm):
            calls.append((model, transcript, pcm.tolist()))
            return [types.SimpleNamespace(text="hello", start=0, end=1)]

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

        self.assertEqual(calls, [("aligner.bin", "hello", [0.0])])


if __name__ == "__main__":
    unittest.main()
