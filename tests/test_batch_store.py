import tempfile
import unittest
from pathlib import Path

from backend.batch_store import DEFAULT_OPTIONS, BatchStore


class BatchStoreTest(unittest.TestCase):
    def test_enqueue_resume_and_options(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BatchStore(Path(directory) / "app.db")
            ids = store.add(1, ["https://a", "https://b"])
            self.assertEqual([row["status"] for row in store.list_for_user(1)], ["pending", "pending"])

            store.update(ids[0], status="asr", file_id="f" * 32)
            self.assertEqual(store.list_for_user(1)[0]["file_id"], "f" * 32)
            store.resume_inflight()
            self.assertEqual(store.list_for_user(1)[0]["status"], "pending")

            self.assertEqual(store.options(1), DEFAULT_OPTIONS)
            store.set_options(1, {"quality": "720", "target_language": "English"})
            options = store.options(1)
            self.assertEqual(options["quality"], "720")
            self.assertEqual(options["target_language"], "English")
            self.assertEqual(options["mode"], "standard")


if __name__ == "__main__":
    unittest.main()
