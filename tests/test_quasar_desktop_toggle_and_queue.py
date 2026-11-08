import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import Quasar


class DesktopToggleTests(unittest.TestCase):
    def test_system_prompt_only_includes_desktop_instructions_when_enabled(self) -> None:
        enabled = Quasar.build_system_prompt(True)
        disabled = Quasar.build_system_prompt(False)

        self.assertIn("DESKTOP CONTROLS", enabled)
        self.assertNotIn("DESKTOP CONTROLS", disabled)
        self.assertGreater(len(enabled), len(disabled))

    def test_sync_adds_and_removes_desktop_tool_schemas(self) -> None:
        desktop_schema = Quasar.agent.tool_schema(
            "desktop_capture",
            "Capture the desktop.",
            {},
            [],
        )
        other_schema = Quasar.agent.tool_schema(
            "message_to_user",
            "Send a message.",
            {},
            [],
        )
        app = SimpleNamespace(
            desktop_tool_schemas=[desktop_schema],
            desktop_control_enabled=False,
        )

        with patch.object(Quasar.agent, "TOOLS", [other_schema]):
            Quasar.QuasarDesktopApp._sync_desktop_tools(app)
            self.assertEqual(Quasar.agent.TOOLS, [other_schema])

            app.desktop_control_enabled = True
            Quasar.QuasarDesktopApp._sync_desktop_tools(app)
            self.assertIn(desktop_schema, Quasar.agent.TOOLS)
            self.assertIn(other_schema, Quasar.agent.TOOLS)

            app.desktop_control_enabled = False
            Quasar.QuasarDesktopApp._sync_desktop_tools(app)
            self.assertEqual(Quasar.agent.TOOLS, [other_schema])


class PromptQueueTests(unittest.TestCase):
    def test_busy_submit_queues_prompt_instead_of_ignoring_it(self) -> None:
        app = SimpleNamespace(
            speech_worker=None,
            model_selector=Mock(),
            model="test-model",
            composer=Mock(),
            pending_voice_audio_b64="",
            busy=True,
            _enqueue_prompt=Mock(),
            _begin_prompt=Mock(),
        )
        app.model_selector.currentText.return_value = "test-model"
        app.composer.toPlainText.return_value = "두 번째 작업"

        Quasar.QuasarDesktopApp.submit(app)

        app.composer.clear.assert_called_once_with()
        app._enqueue_prompt.assert_called_once_with("두 번째 작업", "")
        app._begin_prompt.assert_not_called()

    def test_idle_submit_starts_prompt_immediately(self) -> None:
        app = SimpleNamespace(
            speech_worker=None,
            model_selector=Mock(),
            model="test-model",
            client=object(),
            composer=Mock(),
            pending_voice_audio_b64="",
            busy=False,
            startup_error=None,
            _enqueue_prompt=Mock(),
            _begin_prompt=Mock(return_value=True),
        )
        app.model_selector.currentText.return_value = "test-model"
        app.composer.toPlainText.return_value = "첫 번째 작업"

        Quasar.QuasarDesktopApp.submit(app)

        app._begin_prompt.assert_called_once_with("첫 번째 작업", "")
        app._enqueue_prompt.assert_not_called()

    def test_enqueue_prompt_reports_queue_size(self) -> None:
        app = SimpleNamespace(
            pending_prompts=deque(),
            _add_activity_message=Mock(),
            status_label=Mock(),
        )

        Quasar.QuasarDesktopApp._enqueue_prompt(app, "작업 A", "")
        Quasar.QuasarDesktopApp._enqueue_prompt(app, "작업 B", "")

        self.assertEqual(
            list(app.pending_prompts),
            [("작업 A", ""), ("작업 B", "")],
        )
        self.assertEqual(app._add_activity_message.call_count, 2)
        self.assertIn("2개", app.status_label.setText.call_args.args[0])

    def test_start_next_pending_prompt_skips_failed_entries(self) -> None:
        app = SimpleNamespace(
            closing=False,
            busy=False,
            pending_prompts=deque([
                ("작업 A", ""),
                ("작업 B", ""),
                ("작업 C", ""),
            ]),
            _begin_prompt=Mock(side_effect=[False, False, True]),
        )

        started = Quasar.QuasarDesktopApp._start_next_pending_prompt(app)

        self.assertTrue(started)
        self.assertEqual(app.pending_prompts, deque())
        self.assertEqual(
            app._begin_prompt.call_args_list,
            [call("작업 A", ""), call("작업 B", ""), call("작업 C", "")],
        )

    def test_start_next_pending_prompt_does_nothing_while_busy(self) -> None:
        app = SimpleNamespace(
            closing=False,
            busy=True,
            pending_prompts=deque([("작업 A", "")]),
            _begin_prompt=Mock(),
        )

        started = Quasar.QuasarDesktopApp._start_next_pending_prompt(app)

        self.assertFalse(started)
        self.assertEqual(list(app.pending_prompts), [("작업 A", "")])
        app._begin_prompt.assert_not_called()

    def test_finish_with_response_starts_next_queued_prompt(self) -> None:
        app = SimpleNamespace(
            closing=False,
            messages=[],
            _save_chat_history=Mock(),
            _clear_request_state=Mock(),
            _add_message=Mock(),
            _set_busy=Mock(),
            voice_conversation_active=False,
            voice_response_toggle=Mock(),
            pending_speech_messages=deque(),
            _start_next_pending_speech=Mock(return_value=False),
            _start_next_pending_prompt=Mock(return_value=True),
            _start_voice_conversation_capture=Mock(),
        )
        app.voice_response_toggle.isChecked.return_value = False

        Quasar.QuasarDesktopApp._finish_with_response(
            app,
            "응답",
            [{"role": "system", "content": "system"}],
        )

        app._start_next_pending_prompt.assert_called_once_with()

    def test_cancelled_request_clears_pending_prompts(self) -> None:
        app = SimpleNamespace(
            closing=False,
            _clear_request_state=Mock(),
            pending_speech_messages=deque(["음성"]),
            pending_prompts=deque([("작업 A", ""), ("작업 B", "")]),
            status_label=Mock(),
            _set_busy=Mock(),
            _start_next_pending_speech=Mock(return_value=False),
            voice_conversation_active=False,
        )

        Quasar.QuasarDesktopApp._finish_cancelled_request(app)

        self.assertEqual(app.pending_prompts, deque())
        self.assertEqual(app.pending_speech_messages, deque())


if __name__ == "__main__":
    unittest.main()
