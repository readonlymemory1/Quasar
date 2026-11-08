import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

import Quasar


class ToolContextRoutingTests(unittest.TestCase):
    def test_session_for_tool_context_defaults_to_owner(self) -> None:
        owner = SimpleNamespace()

        self.assertIs(Quasar._session_for_tool_context(owner), owner)

    def test_session_for_tool_context_prefers_thread_session(self) -> None:
        session = SimpleNamespace()
        owner = SimpleNamespace(_tool_context=threading.local())
        owner._tool_context.session = session

        self.assertIs(Quasar._session_for_tool_context(owner), session)

    def test_run_tool_routes_user_message_to_context_session(self) -> None:
        owner_signals = SimpleNamespace(agent_message=Mock())
        session_signals = SimpleNamespace(agent_message=Mock())
        session = SimpleNamespace(signals=session_signals)
        owner = SimpleNamespace(
            signals=owner_signals,
            _tool_context=threading.local(),
            original_run_tool=Mock(),
            original_tool_approval_callback=None,
        )
        owner._tool_context.session = session

        result = Quasar.QuasarDesktopApp._run_tool(
            owner,
            "message_to_user",
            {"message": "진행 상황", "kind": "progress"},
        )

        session_signals.agent_message.emit.assert_called_once_with(
            "진행 상황", "progress"
        )
        owner_signals.agent_message.emit.assert_not_called()
        self.assertIn("메시지", result)

    def test_run_tool_without_context_uses_owner_signals(self) -> None:
        owner_signals = SimpleNamespace(agent_message=Mock())
        owner = SimpleNamespace(
            signals=owner_signals,
            original_run_tool=Mock(),
        )

        Quasar.QuasarDesktopApp._run_tool(
            owner,
            "message_to_user",
            {"message": "hello", "kind": "progress"},
        )

        owner_signals.agent_message.emit.assert_called_once_with(
            "hello", "progress"
        )

    def test_execute_cmd_approval_shows_the_command_text(self) -> None:
        captured = {}

        class CaptureSignal:
            def emit(self, _name, details, request):
                captured["details"] = details
                request["approved"] = True
                request["event"].set()

        session = SimpleNamespace(
            signals=SimpleNamespace(
                tool_approval_requested=CaptureSignal(),
            )
        )
        owner = SimpleNamespace(_tool_context=threading.local())
        owner._tool_context.session = session

        result = Quasar.QuasarDesktopApp._request_tool_approval(
            owner,
            "execute_cmd",
            {"command": "rm -rf build"},
        )

        self.assertTrue(result)
        self.assertIn("rm -rf build", captured["details"])

    def test_request_tool_approval_routes_to_context_session(self) -> None:
        approved = {}

        class AutoApproveSignal:
            def emit(self, _name, _details, request):
                approved["session"] = True
                request["approved"] = True
                request["event"].set()

        session = SimpleNamespace(
            signals=SimpleNamespace(
                tool_approval_requested=AutoApproveSignal(),
            )
        )
        owner = SimpleNamespace(_tool_context=threading.local())
        owner._tool_context.session = session

        result = Quasar.QuasarDesktopApp._request_tool_approval(
            owner,
            "execute_cmd",
            {"command": "echo hi"},
        )

        self.assertTrue(result)
        self.assertTrue(approved.get("session"))


class TaskWindowQueueTests(unittest.TestCase):
    def _window(self, **overrides):
        attributes = dict(
            closing=False,
            busy=False,
            pending_prompts=deque(),
            messages=[{"role": "system", "content": "system"}],
            model="test-model",
            _add_activity_message=Mock(),
            _refresh_status=Mock(),
            _add_message=Mock(),
            _clear_request_state=Mock(),
            _set_busy=Mock(),
            _begin_prompt=Mock(return_value=True),
            _start_next_pending_prompt=Mock(return_value=False),
        )
        attributes.update(overrides)
        return SimpleNamespace(**attributes)

    def test_busy_submit_enqueues_prompt(self) -> None:
        window = self._window(
            busy=True,
            composer=Mock(),
            model_selector=Mock(),
            _enqueue_prompt=Mock(),
            _begin_prompt=Mock(),
        )
        window.model_selector.currentText.return_value = "test-model"
        window.composer.toPlainText.return_value = "병렬 작업"

        Quasar.TaskWindow.submit(window)

        window.composer.clear.assert_called_once_with()
        window._enqueue_prompt.assert_called_once_with("병렬 작업")
        window._begin_prompt.assert_not_called()

    def test_idle_submit_starts_prompt(self) -> None:
        window = self._window(
            busy=False,
            composer=Mock(),
            model_selector=Mock(),
            _begin_prompt=Mock(return_value=True),
            _enqueue_prompt=Mock(),
        )
        window.model_selector.currentText.return_value = "test-model"
        window.composer.toPlainText.return_value = "새 작업"

        Quasar.TaskWindow.submit(window)

        window._begin_prompt.assert_called_once_with("새 작업")
        window._enqueue_prompt.assert_not_called()

    def test_enqueue_prompt_tracks_queue_size(self) -> None:
        window = self._window()

        Quasar.TaskWindow._enqueue_prompt(window, "작업 A")
        Quasar.TaskWindow._enqueue_prompt(window, "작업 B")

        self.assertEqual(
            list(window.pending_prompts),
            [("작업 A", ""), ("작업 B", "")],
        )
        self.assertEqual(window._add_activity_message.call_count, 2)
        self.assertEqual(window._refresh_status.call_count, 2)

    def test_start_next_pending_prompt_skips_failed_entries(self) -> None:
        window = self._window(
            pending_prompts=deque([("작업 A", ""), ("작업 B", ""), ("작업 C", "")]),
            _begin_prompt=Mock(side_effect=[False, False, True]),
        )

        started = Quasar.TaskWindow._start_next_pending_prompt(window)

        self.assertTrue(started)
        self.assertEqual(window.pending_prompts, deque())

    def test_start_next_pending_prompt_stops_while_busy(self) -> None:
        window = self._window(
            busy=True,
            pending_prompts=deque([("작업 A", "")]),
            _begin_prompt=Mock(),
        )

        started = Quasar.TaskWindow._start_next_pending_prompt(window)

        self.assertFalse(started)
        self.assertEqual(list(window.pending_prompts), [("작업 A", "")])
        window._begin_prompt.assert_not_called()

    def test_finish_with_response_starts_next_queued_prompt(self) -> None:
        window = self._window(
            _start_next_pending_prompt=Mock(return_value=True),
        )

        Quasar.TaskWindow._finish_with_response(
            window,
            "응답",
            [{"role": "system", "content": "system"}],
        )

        self.assertEqual(
            window.messages,
            [{"role": "system", "content": "system"}],
        )
        window._clear_request_state.assert_called_once_with()
        window._set_busy.assert_called_once_with(False)
        window._start_next_pending_prompt.assert_called_once_with()

    def test_cancelled_request_clears_queue(self) -> None:
        window = self._window(
            pending_prompts=deque([("작업 A", ""), ("작업 B", "")]),
        )

        Quasar.TaskWindow._finish_cancelled_request(window)

        self.assertEqual(window.pending_prompts, deque())
        window._set_busy.assert_called_once_with(False)


class TaskWindowWorkerTests(unittest.TestCase):
    def test_request_worker_marks_and_clears_thread_session(self) -> None:
        service = SimpleNamespace(
            _tool_context=threading.local(),
            live_analysis_server=None,
            mcp_manager=None,
            max_tool_rounds=8,
            tool_calling_mode="dynamic",
        )
        signals = SimpleNamespace(
            request_progress=Mock(),
            request_cancelled=Mock(),
            request_failed=Mock(),
            response_ready=Mock(),
        )
        window = SimpleNamespace(
            app=service,
            signals=signals,
            closing=False,
        )
        client = Mock()
        observed = {}

        def fake_request_agent(*_args, **_kwargs):
            observed["during"] = service._tool_context.session
            return "답변"

        with patch.object(
            Quasar.agent,
            "request_agent",
            side_effect=fake_request_agent,
        ):
            Quasar.TaskWindow._request_worker(
                window,
                client,
                "gpt-4o",
                "질문",
                [{"role": "system", "content": "system"}],
                threading.Event(),
            )

        self.assertIs(observed["during"], window)
        self.assertIsNone(service._tool_context.session)
        signals.response_ready.emit.assert_called_once()
        client.close.assert_called_once_with()

    def test_request_worker_reports_failure(self) -> None:
        service = SimpleNamespace(
            _tool_context=threading.local(),
            live_analysis_server=None,
            mcp_manager=None,
            max_tool_rounds=8,
            tool_calling_mode="dynamic",
        )
        signals = SimpleNamespace(
            request_progress=Mock(),
            request_cancelled=Mock(),
            request_failed=Mock(),
            response_ready=Mock(),
        )
        window = SimpleNamespace(app=service, signals=signals, closing=False)

        with patch.object(
            Quasar.agent,
            "request_agent",
            side_effect=RuntimeError("boom"),
        ):
            Quasar.TaskWindow._request_worker(
                window,
                Mock(),
                "gpt-4o",
                "질문",
                [{"role": "system", "content": "system"}],
                threading.Event(),
            )

        signals.request_failed.emit.assert_called_once()
        signals.response_ready.emit.assert_not_called()
        self.assertIsNone(service._tool_context.session)


if __name__ == "__main__":
    unittest.main()
