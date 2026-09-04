import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.logging_config as logging_config
from backend.jobs import JobRunner, JobStore


class LoggingConfigTest(unittest.TestCase):
    def tearDown(self):
        logging_config.shutdown_logging()

    def test_console_and_rotating_file_are_configured_once(self):
        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                logger = logging_config.configure_logging(directory)
                logger.info("hello api_key=secret")
                file_handler = next(handler for handler in logger.handlers if hasattr(handler, "maxBytes"))
                self.assertEqual((file_handler.maxBytes, file_handler.backupCount), (10 * 1024 * 1024, 5))
                before = list(logger.handlers)
                logging_config.configure_logging(directory)
                self.assertEqual(list(logger.handlers), before)
                logging_config.shutdown_logging()
            output = (Path(directory) / "data" / "logs" / "ducklingo.log").read_text("utf-8")
            self.assertIn("hello", stderr.getvalue())
            self.assertIn("hello", output)
            self.assertNotIn("secret", stderr.getvalue() + output)


class JobLoggingTest(unittest.TestCase):
    def test_lifecycle_logs_user_stage_and_failure(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory, patch("backend.jobs.log") as logger:
                runner = JobRunner(JobStore(Path(directory) / "jobs.db"))

                async def work(report):
                    report("extract")

                await runner.enqueue("a" * 32, "asr", 7, "cloud", work, username="alice")
                messages = [call.args[0] for call in logger.info.call_args_list]
                self.assertTrue(any("job queued" in message for message in messages))
                self.assertTrue(any("job stage" in message for message in messages))
                self.assertTrue(any("job completed" in message for message in messages))

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()
