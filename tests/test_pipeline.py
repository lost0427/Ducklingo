import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pipeline import run_translate


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_duration_subtitles_fail_before_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.pipeline.resolve_ffmpeg"), patch(
                "backend.pipeline.probe_duration", return_value=10
            ), patch("backend.pipeline.translate_sentences") as translate:
                with self.assertRaisesRegex(RuntimeError, "no valid subtitle timestamps"):
                    await run_translate(
                        lambda _: None, root, root / "video.mp4", root, "file", 1,
                        [{"text": "hello", "start": 0.23, "end": 0.23}],
                        {}, {}, "Chinese", "standard", "off", 30,
                    )
                translate.assert_not_called()
                self.assertEqual(list(root.iterdir()), [])
