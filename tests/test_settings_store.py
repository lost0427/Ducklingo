import tempfile
import unittest
from pathlib import Path

from backend.settings_store import GLOBAL, SettingsStore, mask_key


def store_in(directory: str) -> SettingsStore:
    return SettingsStore(Path(directory) / "settings.db")


def fetched(store: SettingsStore, user_id: int, service: str):
    value = store.get(user_id, service)
    assert value is not None
    return value


def resolved(store: SettingsStore, user_id: int, service: str):
    value = store.resolve(user_id, service)
    assert value is not None
    return value


class MaskKeyTest(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(mask_key("sk-1234567890abcdef"), "sk-…cdef")
        self.assertEqual(mask_key("short"), "…")


class SettingsStoreTest(unittest.TestCase):
    def test_put_and_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            store.put(1, "asr", {"kind": "cloud", "base_url": "https://api.mistral.ai", "api_key": "k1", "model": "voxtral-mini-latest"})
            row = fetched(store, 1, "asr")
            self.assertEqual((row["model"], row["kind"]), ("voxtral-mini-latest", "cloud"))

    def test_put_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                store_in(directory).put(1, "asr", {"nope": "x"})

    def test_resolve_user_overrides_global_per_field(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            self.assertIsNone(store.resolve(7, "asr"))
            store.put(GLOBAL, "asr", {"kind": "cloud", "base_url": "https://global", "api_key": "gk", "model": "m-global"})
            store.put(7, "asr", {"model": "m-user"})
            self.assertEqual(
                resolved(store, 7, "asr"),
                {"kind": "cloud", "base_url": "https://global", "api_key": "gk", "model": "m-user"},
            )
            self.assertEqual(resolved(store, 8, "asr")["model"], "m-global")

    def test_services_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            store.put(1, "asr", {"model": "asr-model"})
            store.put(1, "sentence", {"model": "llm-model"})
            self.assertEqual(resolved(store, 1, "asr")["model"], "asr-model")
            self.assertEqual(resolved(store, 1, "sentence")["model"], "llm-model")

    def test_empty_user_value_inherits_global(self):
        with tempfile.TemporaryDirectory() as directory:
            store = store_in(directory)
            store.put(GLOBAL, "sentence", {"base_url": "https://global", "api_key": "gk"})
            store.put(3, "sentence", {"api_key": ""})
            self.assertEqual(resolved(store, 3, "sentence"), {"base_url": "https://global", "api_key": "gk"})


if __name__ == "__main__":
    unittest.main()
