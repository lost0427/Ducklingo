import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.batch_scheduler import PipelineScheduler
from backend.batch_store import BatchStore
from backend.media_store import MediaStore
from backend.settings_store import SettingsStore


class PipelineSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_item_runs_through_all_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = BatchStore(root / "app.db")
            media = MediaStore(root / "app.db")
            settings = SettingsStore(root / "app.db")
            settings.put(1, "asr", {"kind": "local", "model": "m"})
            settings.put(1, "sentence", {"base_url": "https://llm/v1", "api_key": "k", "model": "s"})
            settings.put(1, "translate", {"base_url": "https://llm/v1", "api_key": "k", "model": "t"})
            slots = {name: asyncio.Semaphore(1) for name in ("download", "local", "cloud", "llm")}
            scheduler = PipelineScheduler(batch, media, settings, root, slots)
            calls = []

            def fake_download(url, output, *args, **kwargs):
                Path(output).write_bytes(b"video")

            async def fake_asr(report, scheduler_root, source, directory_, file_id, settings_, section):
                calls.append("asr")
                (Path(directory_) / f"{file_id}.asr.json").write_text('{"kind": "local", "words": []}', encoding="utf-8")

            async def fake_sentence(report, directory_, file_id, words, settings_, section, timeout):
                calls.append("sentence")
                (Path(directory_) / f"{file_id}.sentences.json").write_text('{"batches": []}', encoding="utf-8")

            async def fake_translate(report, scheduler_root, source, directory_, file_id, user_id, sentences, settings_, section, target, mode, effort, timeout):
                calls.append("translate")
                archive = Path(directory_) / "out.zip"
                archive.write_bytes(b"zip")
                (Path(directory_) / f"{file_id}.translation.json").write_text(json.dumps({"archive": archive.name}), encoding="utf-8")

            item_id = batch.add(1, ["https://video"])[0]
            with patch("backend.batch_scheduler.download_from_config", fake_download), patch(
                "backend.batch_scheduler.media_title", return_value=None
            ), patch("backend.batch_scheduler.run_asr", fake_asr), patch(
                "backend.batch_scheduler.run_sentence", fake_sentence
            ), patch("backend.batch_scheduler.run_translate", fake_translate):
                scheduler.submit(item_id)
                await asyncio.wait_for(scheduler.tasks[item_id], timeout=5)

            self.assertEqual(batch.list_for_user(1)[0]["status"], "done")
            self.assertEqual(calls, ["asr", "sentence", "translate"])


if __name__ == "__main__":
    unittest.main()
