import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import ANY, Mock, patch

import Quasar


class OfficeMonitorGuiTests(unittest.TestCase):
    def test_cli_handler_returns_help_to_gui_output_callback(self) -> None:
        output: list[str] = []

        Quasar.agent.handle_cli_command(
            "/help",
            [{"role": "system", "content": "system prompt"}],
            output=output.append,
        )

        self.assertEqual(len(output), 1)
        self.assertIn("/collab on|off", output[0])

    def test_chat_command_captures_cli_output(self) -> None:
        messages = [{"role": "system", "content": "system prompt"}]
        app = SimpleNamespace(messages=messages)

        with patch.object(
            Quasar.agent,
            "handle_cli_command",
            side_effect=lambda command, _messages, output, **_kwargs: output(
                f"handled {command}"
            ),
        ) as handle_command:
            result = Quasar.QuasarDesktopApp._run_chat_command(
                app,
                "/help",
            )

        self.assertEqual(result, "handled /help")
        handle_command.assert_called_once_with(
            "/help",
            messages,
            collaboration_monitor=None,
            output=ANY,
        )

    def test_collaboration_command_uses_gui_office_monitor(self) -> None:
        monitor = Mock()
        app = SimpleNamespace(
            messages=[],
            _ensure_office_monitor=Mock(return_value=monitor),
        )
        with patch.object(
            Quasar.agent,
            "handle_cli_command",
            side_effect=lambda _command, _messages, output, **_kwargs: output(
                "감시 시작"
            ),
        ) as handle_command:
            result = Quasar.QuasarDesktopApp._run_chat_command(
                app,
                "/collab on",
            )

        self.assertEqual(result, "감시 시작")
        app._ensure_office_monitor.assert_called_once_with()
        handle_command.assert_called_once_with(
            "/collab on",
            [],
            collaboration_monitor=monitor,
            output=ANY,
        )

    def test_enable_failure_unchecks_control(self) -> None:
        checkbox = Mock()
        checkbox.blockSignals.side_effect = [False, True]

        def fail_to_create_monitor():
            raise RuntimeError("model unavailable")

        app = SimpleNamespace(
            closing=False,
            _ensure_office_monitor=fail_to_create_monitor,
        )
        with patch.object(Quasar.QMessageBox, "warning") as show_warning:
            Quasar.QuasarDesktopApp._set_office_monitor_enabled(
                app,
                True,
                checkbox,
            )

        checkbox.setChecked.assert_called_once_with(False)
        self.assertEqual(checkbox.blockSignals.call_count, 2)
        show_warning.assert_called_once()

    def test_dispose_stops_monitor_and_closes_its_client(self) -> None:
        monitor = Mock()
        client = Mock()
        app = SimpleNamespace(
            office_monitor=monitor,
            office_monitor_client=client,
            office_monitor_source=object(),
        )

        Quasar.QuasarDesktopApp._dispose_office_monitor(app)

        monitor.set_enabled.assert_called_once_with(False)
        monitor.close.assert_called_once_with()
        client.close.assert_called_once_with()
        self.assertIsNone(app.office_monitor)
        self.assertIsNone(app.office_monitor_client)
        self.assertIsNone(app.office_monitor_source)

    def test_model_change_stops_active_monitor(self) -> None:
        monitor = Mock()
        monitor.is_enabled.return_value = True
        client = Mock()
        app = SimpleNamespace(
            office_monitor=monitor,
            office_monitor_client=client,
            office_monitor_source=object(),
            model="old-model",
            status_label=Mock(),
        )
        app._dispose_office_monitor = MethodType(
            Quasar.QuasarDesktopApp._dispose_office_monitor,
            app,
        )
        app._reset_office_monitor = MethodType(
            Quasar.QuasarDesktopApp._reset_office_monitor,
            app,
        )

        Quasar.QuasarDesktopApp._remember_model(app, "new-model")

        self.assertEqual(app.model, "new-model")
        monitor.set_enabled.assert_called_once_with(False)
        monitor.close.assert_called_once_with()
        client.close.assert_called_once_with()
        app.status_label.setText.assert_called_once_with(
            "AI 모델이 변경되어 Word/PPT 감시를 중지했습니다. 다시 켜 주세요."
        )

    def test_poll_routes_questions_and_proposals_to_chat(self) -> None:
        monitor = Mock()
        monitor.take_pending_questions.return_value = ["문서 질문"]
        monitor.take_pending_office_proposals.return_value = [
            (7, {"path": "notes.docx"})
        ]
        app = SimpleNamespace(
            closing=False,
            office_monitor=monitor,
            _add_message=Mock(),
            _show_office_proposal=Mock(),
        )

        Quasar.QuasarDesktopApp._poll_office_monitor(app)

        app._add_message.assert_called_once_with("assistant", "문서 질문")
        app._show_office_proposal.assert_called_once_with(
            7,
            {"path": "notes.docx"},
        )


if __name__ == "__main__":
    unittest.main()
