import tempfile
import unittest
from pathlib import Path

from todo_store import TodoStore


class TodoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TodoStore(Path(self.temp_dir.name) / "todos.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_list_update_and_delete_task(self) -> None:
        task = self.store.add_task(
            "  보고서 작성  ",
            "초안 만들기",
            "2026-10-30",
            "high",
        )
        self.assertEqual(task["title"], "보고서 작성")
        self.assertFalse(task["completed"])
        self.assertEqual(len(self.store.list_tasks("active")), 1)

        updated = self.store.update_task(task["id"], {"completed": True})
        self.assertTrue(updated["completed"])
        self.assertEqual(self.store.list_tasks("active"), [])
        self.assertEqual(len(self.store.list_tasks("completed")), 1)

        self.store.delete_task(task["id"])
        self.assertEqual(self.store.list_tasks(), [])

    def test_rejects_invalid_task_data(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_task("  ")
        with self.assertRaises(ValueError):
            self.store.add_task("테스트", due_date="2026-13-40")
        with self.assertRaises(ValueError):
            self.store.add_task("테스트", priority="urgent")

    def test_missing_task_update_is_reported(self) -> None:
        with self.assertRaises(KeyError):
            self.store.update_task(999, {"completed": True})


if __name__ == "__main__":
    unittest.main()
