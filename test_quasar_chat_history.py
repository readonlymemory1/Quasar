import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from chat_history import ChatHistoryStore
import Quasar


class ChatHistoryStoreTests(unittest.TestCase):
    def test_saves_and_restores_text_conversation_with_prompt_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ChatHistoryStore(Path(temporary_directory) / "chats.sqlite3")

            chat_id = store.save(
                None,
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "  Find my recent reports  "},
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "secret"}}],
                    },
                    {"role": "assistant", "content": "보고서를 찾았습니다."},
                    {"role": "tool", "content": "internal tool result"},
                ],
            )

            conversation = store.get(chat_id)

        self.assertIsNotNone(conversation)
        self.assertEqual(conversation["title"], "Find my recent reports")
        self.assertEqual(
            conversation["messages"],
            [
                {"role": "user", "content": "  Find my recent reports  "},
                {"role": "assistant", "content": "보고서를 찾았습니다."},
            ],
        )

    def test_recent_conversations_are_ordered_by_last_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ChatHistoryStore(Path(temporary_directory) / "chats.sqlite3")
            first = store.save(None, [{"role": "user", "content": "first"}])
            second = store.save(None, [{"role": "user", "content": "second"}])
            store.save(first, [{"role": "user", "content": "first"}, {"role": "assistant", "content": "updated"}])

            recent = store.list_recent()

        self.assertEqual([item["id"] for item in recent], [first, second])

    def test_delete_all_removes_saved_conversations_and_returns_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ChatHistoryStore(Path(temporary_directory) / "chats.sqlite3")
            store.save(None, [{"role": "user", "content": "first"}])
            store.save(None, [{"role": "user", "content": "second"}])

            deleted_count = store.delete_all()

            self.assertEqual(deleted_count, 2)
            self.assertEqual(store.list_recent(), [])

    def test_delete_removes_only_the_selected_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ChatHistoryStore(Path(temporary_directory) / "chats.sqlite3")
            first = store.save(None, [{"role": "user", "content": "first"}])
            second = store.save(None, [{"role": "user", "content": "second"}])

            deleted = store.delete(first)

            self.assertTrue(deleted)
            self.assertIsNone(store.get(first))
            self.assertIsNotNone(store.get(second))
            self.assertFalse(store.delete(first))


class TemporaryChatTests(unittest.TestCase):
    def test_temporary_chat_restores_previous_conversation_without_saving_temp(self) -> None:
        original_messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "saved message"},
        ]
        app = SimpleNamespace(
            busy=False,
            temporary_chat=False,
            current_chat_id="saved-chat",
            messages=original_messages,
            suspended_chat=None,
            status_label=Mock(),
            _save_chat_history=Mock(),
            _clear_chat_view=Mock(),
            _update_chat_mode_ui=Mock(),
            _add_message=Mock(),
        )

        Quasar.QuasarDesktopApp._toggle_temporary_chat(app)
        app.messages.append({"role": "user", "content": "temporary only"})
        Quasar.QuasarDesktopApp._toggle_temporary_chat(app)

        app._save_chat_history.assert_called_once_with()
        self.assertFalse(app.temporary_chat)
        self.assertEqual(app.current_chat_id, "saved-chat")
        self.assertEqual(app.messages, original_messages)
        app._add_message.assert_called_once_with("user", "saved message")
        app._update_chat_mode_ui.assert_called_with()


if __name__ == "__main__":
    unittest.main()
