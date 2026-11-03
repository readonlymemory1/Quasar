import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import ANY, Mock, patch

import Quasar


class OfficeMonitorGuiTests(unittest.TestCase):
    def test_checklist_panel_toggle_refreshes_shared_tasks(self) -> None:
        app = SimpleNamespace(
            checklist_panel=Mock(),
            checklist_button=Mock(),
            _refresh_checklist=Mock(),
        )

        Quasar.QuasarDesktopApp._set_checklist_visible(app, True)

        app.checklist_panel.setVisible.assert_called_once_with(True)
        app.checklist_button.setText.assert_called_once_with("체크리스트 닫기")
        app._refresh_checklist.assert_called_once_with()

    def test_checklist_completion_updates_shared_todo_store(self) -> None:
        item = Mock()
        item.data.return_value = 17
        item.checkState.return_value = Quasar.Qt.CheckState.Checked
        app = SimpleNamespace(
            checklist_status=Mock(),
            _refresh_checklist=Mock(),
        )

        with patch.object(Quasar.agent.todo_store, "update_task") as update_task:
            Quasar.QuasarDesktopApp._set_checklist_item_completed(app, item)

        update_task.assert_called_once_with(17, {"completed": True})
        app._refresh_checklist.assert_called_once_with()

    def test_chat_scrolls_to_bottom_after_layout_is_updated(self) -> None:
        scrollbar = Mock()
        scrollbar.maximum.return_value = 420
        app = SimpleNamespace(
            chat_scroll=SimpleNamespace(
                verticalScrollBar=Mock(return_value=scrollbar),
            ),
        )

        Quasar.QuasarDesktopApp._scroll_chat_to_bottom(app, 0, 420)

        scrollbar.setValue.assert_called_once_with(420)

    def test_radial_menu_close_finishes_after_reverse_animation(self) -> None:
        timer = Mock()
        overlay = Mock()
        menu_button = Mock()
        radial_button = Mock()
        app = SimpleNamespace(
            radial_animation_timer=timer,
            radial_animation_progress=1.0,
            radial_animation_from_progress=0.0,
            radial_animation_to_progress=0.0,
            radial_animation_opening=True,
            radial_animation_started_at=10.0,
            radial_animation_duration_ms=480,
            radial_buttons=[radial_button],
            control_overlay=overlay,
            menu_button=menu_button,
            _layout_radial_menu=Mock(),
            _finish_radial_animation=lambda: (
                Quasar.QuasarDesktopApp._finish_radial_animation(app)
            ),
        )

        with patch(
            "Quasar.time.perf_counter",
            side_effect=(10.0, 10.5),
        ):
            Quasar.QuasarDesktopApp._start_radial_animation(app, False)
            Quasar.QuasarDesktopApp._advance_radial_animation(app)

        overlay.hide.assert_called_once_with()
        menu_button.setText.assert_called_once_with("☰")
        radial_button.setEnabled.assert_called_once_with(False)
        app._layout_radial_menu.assert_called()
        timer.stop.assert_called()

    def test_radial_menu_origin_uses_left_orb_position(self) -> None:
        orb = Mock()
        orb.rect.return_value.center.return_value = "orb-center"
        orb.mapToGlobal.return_value = "global-orb-center"
        overlay = Mock()
        overlay.mapFromGlobal.return_value = Quasar.QPointF(34, 82).toPoint()
        app = SimpleNamespace(orb=orb, control_overlay=overlay)

        origin = Quasar.QuasarDesktopApp._radial_menu_origin(app)

        self.assertEqual(origin, Quasar.QPointF(34, 82))
        orb.mapToGlobal.assert_called_once_with("orb-center")
        overlay.mapFromGlobal.assert_called_once_with("global-orb-center")

    def test_request_progress_updates_status_without_creating_chat_bubble(self) -> None:
        app = SimpleNamespace(
            closing=False,
            request_cancel_pending=False,
            status_label=Mock(),
            _add_activity_message=Mock(),
        )

        Quasar.QuasarDesktopApp._show_request_progress(
            app,
            "도구 실행 중: 사용자 메시지 전송",
        )

        app.status_label.setText.assert_called_once_with(
            "도구 실행 중: 사용자 메시지 전송"
        )
        app._add_activity_message.assert_called_once_with(
            "도구 실행 중: 사용자 메시지 전송"
        )

    def test_activity_message_is_plain_text_and_consecutive_duplicates_are_skipped(self) -> None:
        layout = Mock()
        layout.count.return_value = 1
        row = Mock()
        activity_label = Mock()
        app = SimpleNamespace(
            last_activity_message=None,
            chat_layout=layout,
            _scroll_chat_to_bottom=Mock(),
        )

        with (
            patch.object(
                Quasar,
                "QLabel",
                return_value=activity_label,
            ) as create_label,
            patch.object(Quasar, "QHBoxLayout", return_value=row),
            patch.object(Quasar.QTimer, "singleShot") as schedule_scroll,
        ):
            Quasar.QuasarDesktopApp._add_activity_message(
                app,
                "도구 실행 중: web_click",
            )
            Quasar.QuasarDesktopApp._add_activity_message(
                app,
                "도구 실행 중: web_click",
            )
        layout.insertLayout.assert_called_once()
        layout.insertLayout.assert_called_once()
        self.assertEqual(layout.insertLayout.call_args.args[1], row)
        create_label.assert_called_once_with("도구 실행 중: web_click")
        activity_label.setObjectName.assert_called_once_with("activityMessage")
        row.addWidget.assert_called_once_with(activity_label)
        self.assertEqual(schedule_scroll.call_count, 1)

    def test_agent_progress_and_error_messages_use_chat_bubbles(self) -> None:
        app = SimpleNamespace(
            closing=False,
            request_cancel_pending=False,
            _add_message=Mock(),
        )

        Quasar.QuasarDesktopApp._show_agent_message(app, "작업 중입니다.", "progress")
        Quasar.QuasarDesktopApp._show_agent_message(app, "확인 중입니다.", "error")

        self.assertEqual(
            app._add_message.call_args_list,
            [
                unittest.mock.call("assistant", "작업 중입니다."),
                unittest.mock.call(
                    "assistant",
                    "**오류 및 복구**\n\n확인 중입니다.",
                ),
            ],
        )

    def test_chat_restores_persisted_office_watchlist_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            watchlist_path = workspace / ".quasar" / "collaboration_office.json"
            watchlist_path.parent.mkdir()
            watchlist_path.write_text(
                json.dumps([str(workspace / "notes.docx")]),
                encoding="utf-8",
            )
            monitor = Mock()
            app = SimpleNamespace(
                office_monitor=None,
                _ensure_office_monitor=Mock(return_value=monitor),
            )

            with patch.object(Quasar, "__file__", str(workspace / "Quasar.py")):
                restored = Quasar.QuasarDesktopApp._office_monitor_for_chat(app)

        self.assertIs(restored, monitor)
        app._ensure_office_monitor.assert_called_once_with()

    def test_chat_request_loads_connected_office_context(self) -> None:
        monitor = Mock()
        monitor.office_chat_context.return_value = (
            "\n[연결 문서]\n중요한 사실",
            [],
            1,
        )
        mcp_manager = Mock()
        live_analysis_server = Mock()
        client = Mock()
        signals = SimpleNamespace(
            request_progress=Mock(),
            response_ready=Mock(),
            request_failed=Mock(),
            request_cancelled=Mock(),
        )
        app = SimpleNamespace(
            signals=signals,
            live_analysis_server=live_analysis_server,
        )

        with patch.object(
            Quasar.agent,
            "request_agent",
            return_value="답변",
        ) as request_agent:
            Quasar.QuasarDesktopApp._request_response(
                app,
                client,
                "gpt-4o",
                "연결 문서 질문",
                [{"role": "system", "content": "system prompt"}],
                threading.Event(),
                monitor,
                mcp_manager,
            )

        monitor.office_chat_context.assert_called_once_with()
        signals.request_progress.emit.assert_called_once_with(
            "연결된 문서 1개를 참고합니다."
        )
        self.assertEqual(
            request_agent.call_args.kwargs["office_context"],
            "\n[연결 문서]\n중요한 사실",
        )
        self.assertIs(request_agent.call_args.kwargs["mcp_manager"], mcp_manager)
        self.assertIs(
            request_agent.call_args.kwargs["live_analysis_server"],
            live_analysis_server,
        )
        signals.response_ready.emit.assert_called_once_with(
            "답변",
            [{"role": "system", "content": "system prompt"}],
        )
        client.close.assert_called_once_with()

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
            _close_office_client_after_monitor=(
                Quasar.QuasarDesktopApp._close_office_client_after_monitor
            ),
        )

        Quasar.QuasarDesktopApp._dispose_office_monitor(app)

        monitor.set_enabled.assert_called_once_with(False)
        monitor.close.assert_called_once_with()
        client.close.assert_called_once_with()
        self.assertIsNone(app.office_monitor)
        self.assertIsNone(app.office_monitor_client)
        self.assertIsNone(app.office_monitor_source)

    def test_dispose_defers_client_close_until_active_monitor_stops(self) -> None:
        monitor = Mock()
        monitor.close.return_value = False
        client = Mock()
        app = SimpleNamespace(
            office_monitor=monitor,
            office_monitor_client=client,
            office_monitor_source=object(),
            _close_office_client_after_monitor=(
                Quasar.QuasarDesktopApp._close_office_client_after_monitor
            ),
        )
        cleanup_thread = Mock()
        with patch.object(
            Quasar.threading,
            "Thread",
            return_value=cleanup_thread,
        ) as create_thread:
            Quasar.QuasarDesktopApp._dispose_office_monitor(app)

        client.close.assert_not_called()
        cleanup_thread.start.assert_called_once_with()
        create_thread.assert_called_once()
        arguments = create_thread.call_args.kwargs["args"]
        self.assertIs(arguments[0], monitor)
        self.assertIs(arguments[1], client)

        create_thread.call_args.kwargs["target"](*arguments)

        monitor.wait_until_closed.assert_called_once_with()
        client.close.assert_called_once_with()

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
            "AI 모델이 변경되어 문서 감시를 중지했습니다. 다시 켜 주세요."
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
            _queue_collaboration_speech=Mock(),
        )

        Quasar.QuasarDesktopApp._poll_office_monitor(app)

        app._add_message.assert_called_once_with("assistant", "문서 질문")
        app._queue_collaboration_speech.assert_called_once_with("문서 질문")
        app._show_office_proposal.assert_called_once_with(
            7,
            {"path": "notes.docx"},
        )


if __name__ == "__main__":
    unittest.main()
