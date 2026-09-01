import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.jobs import JobRunner, JobStore, load_job_config


def store_in(directory: str) -> JobStore:
    return JobStore(Path(directory) / "jobs.db")


def fetched(store: JobStore, file_id: str):
    value = store.get(file_id, "asr")
    assert value is not None
    return value


class JobStoreTest(unittest.TestCase):
    def test_lifecycle_and_reenqueue_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            file_id = "a" * 32
            store.enqueue(file_id, "asr", 1)
            row = fetched(store, file_id)
            self.assertEqual((row["status"], row["user_id"], row["stage"]), ("queued", 1, ""))
            store.update(file_id, "asr", status="running", stage="extract")
            self.assertEqual(fetched(store, file_id)["stage"], "extract")
            store.enqueue(file_id, "asr", 2)
            row = fetched(store, file_id)
            self.assertEqual(
                (row["status"], row["stage"], row["error"], row["user_id"]),
                ("queued", "", "", 2),
            )

    def test_fail_stale_marks_queued_running_cancelling(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            for suffix, status in (("a", "running"), ("b", "queued"), ("c", "cancelling")):
                file_id = suffix * 32
                store.enqueue(file_id, "asr", 1)
                store.update(file_id, "asr", status=status)
            self.assertEqual(store.fail_stale(), 3)
            for suffix in ("a", "b", "c"):
                row = fetched(store, suffix * 32)
                self.assertEqual((row["status"], row["error"]), ("error", "interrupted"))
            store.enqueue("d" * 32, "asr", 1)
            store.update("d" * 32, "asr", status="done")
            self.assertEqual(store.fail_stale(), 0)


class JobRunnerTest(unittest.TestCase):
    def test_success_records_stages_and_done(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                store = store_in(directory)
                runner = JobRunner(store)
                stages = []

                async def work(report):
                    report("extract")
                    stages.append(fetched(store, "a" * 32)["stage"])
                    report("transcribe")

                task = runner.enqueue("a" * 32, "asr", 1, "cloud", work)
                await task
                row = fetched(store, "a" * 32)
                self.assertEqual(stages, ["extract"])
                self.assertEqual((row["status"], row["stage"]), ("done", ""))

        asyncio.run(main())

    def test_error_is_captured(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                store = store_in(directory)
                runner = JobRunner(store)

                async def work(report):
                    raise ValueError("boom")

                await runner.enqueue("a" * 32, "asr", 1, "cloud", work)
                row = fetched(store, "a" * 32)
                self.assertEqual((row["status"], row["error"]), ("error", "boom"))

        asyncio.run(main())

    def test_cancel_marks_cancelled(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                store = store_in(directory)
                runner = JobRunner(store)
                started = asyncio.Event()

                async def work(report):
                    started.set()
                    await asyncio.sleep(30)

                task = runner.enqueue("a" * 32, "asr", 1, "local", work)
                await started.wait()
                self.assertTrue(runner.active("a" * 32, "asr"))
                self.assertTrue(runner.cancel("a" * 32, "asr"))
                self.assertEqual(fetched(store, "a" * 32)["status"], "cancelling")
                await task
                self.assertEqual(fetched(store, "a" * 32)["status"], "cancelled")
                self.assertFalse(runner.cancel("a" * 32, "asr"))

        asyncio.run(main())

    def test_reenqueue_while_active_rejected(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                store = store_in(directory)
                runner = JobRunner(store)
                started = asyncio.Event()

                async def work(report):
                    started.set()
                    await asyncio.sleep(30)

                task = runner.enqueue("a" * 32, "asr", 1, "cloud", work)
                await started.wait()
                with self.assertRaises(RuntimeError):
                    runner.enqueue("a" * 32, "asr", 1, "cloud", work)
                runner.cancel("a" * 32, "asr")
                await task

        asyncio.run(main())

    def test_cloud_and_local_queue_independently(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                store = store_in(directory)
                runner = JobRunner(store, cloud_concurrency=1, local_concurrency=1)
                gate = {}
                entered = {}

                def make_work(name):
                    entered[name] = asyncio.Event()
                    gate[name] = asyncio.Event()

                    async def work(report):
                        entered[name].set()
                        await gate[name].wait()

                    return work

                first = runner.enqueue("a" * 32, "asr", 1, "cloud", make_work("first"))
                second = runner.enqueue("b" * 32, "asr", 1, "cloud", make_work("second"))
                local = runner.enqueue("c" * 32, "asr", 1, "local", make_work("local"))
                await entered["first"].wait()
                await asyncio.sleep(0.05)
                self.assertFalse(entered["second"].is_set())
                await entered["local"].wait()
                for name in ("first", "second", "local"):
                    gate[name].set()
                await asyncio.gather(first, second, local)

        asyncio.run(main())

    def test_unknown_provider_rejected(self):
        async def main():
            with tempfile.TemporaryDirectory() as directory:
                runner = JobRunner(store_in(directory))
                with self.assertRaises(ValueError):
                    runner.enqueue("a" * 32, "asr", 1, "quantum", None)

        asyncio.run(main())


class LoadJobConfigTest(unittest.TestCase):
    def test_defaults_without_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_job_config(Path(directory)), {"cloud": 4, "local": 1})

    def test_reads_concurrency_from_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "config.toml").write_text(
                "[asr]\ncloud_concurrency = 7\nlocal_concurrency = 0\n",
                encoding="utf-8",
            )
            self.assertEqual(load_job_config(root), {"cloud": 7, "local": 1})


if __name__ == "__main__":
    unittest.main()
