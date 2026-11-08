import ctypes
import base64
import contextlib
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from PIL import Image

import quasar_cli
from quasar_desktop_control import (
    MAX_ACTIONS_PER_SESSION,
    DesktopControlSession,
    _Input,
    _Point,
    capture_desktop_image,
    encode_desktop_image,
)


@contextmanager
def _mock_dpi_context(user32):
    yield user32


def _mock_user32(cursor_positions, metrics):
    def set_cursor(x, y):
        cursor_positions[:] = [(x, y)]
        return True

    def get_cursor(pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
        point.x, point.y = cursor_positions[-1]
        return True

    return SimpleNamespace(
        GetSystemMetrics=lambda index: metrics[index],
        SetPhysicalCursorPos=set_cursor,
        GetPhysicalCursorPos=get_cursor,
        mouse_event=lambda *_args: None,
    )


class DesktopControlSessionTests(unittest.TestCase):
    def test_control_actions_require_an_active_session(self) -> None:
        session = DesktopControlSession()

        with self.assertRaisesRegex(RuntimeError, "활성화"):
            session.move_mouse(10, 10)

    def test_control_actions_require_a_screenshot_coordinate_frame(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        with patch("quasar_desktop_control.time.monotonic", return_value=100.0):
            with self.assertRaisesRegex(RuntimeError, "먼저 desktop_capture"):
                session.move_mouse(10, 10)

    def test_mouse_actions_reject_coordinates_outside_normalized_range(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        session._capture_size = (1920, 1080)
        with patch("quasar_desktop_control.time.monotonic", return_value=100.0):
            with self.assertRaisesRegex(ValueError, "0..1000"):
                session.move_mouse(1200, 500)

    def test_agent_capture_requires_active_session(self) -> None:
        session = DesktopControlSession()
        with self.assertRaisesRegex(RuntimeError, "활성화"):
            session.capture_for_agent()

    def test_capture_records_the_coordinate_frame_dimensions(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        image = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
        with (
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
            patch("quasar_desktop_control.capture_desktop_image", return_value=image),
        ):
            _, width, height = session.capture_for_agent()
        self.assertEqual((width, height), (1920, 1080))
        self.assertEqual(session._capture_size, (1920, 1080))

    def test_mouse_actions_translate_virtual_screen_coordinates(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        session._capture_size = (1920, 1080)
        cursor_positions: list[tuple[int, int]] = []
        fake_user32 = _mock_user32(
            cursor_positions,
            {76: -100, 77: -50, 78: 1920, 79: 1080},
        )

        with (
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
            patch(
                "quasar_desktop_control._physical_dpi_context",
                return_value=_mock_dpi_context(fake_user32),
            ),
            patch.object(DesktopControlSession, "_user32", return_value=fake_user32),
        ):
            result = session.click(1000, 1000)

        self.assertEqual(cursor_positions, [(1819, 1029)])
        self.assertIn("캡처 좌표 (1919, 1079)", result)

    def test_mouse_coordinates_scale_to_physical_virtual_screen_coordinates(
        self,
    ) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        session._capture_size = (2560, 1440)
        cursor_positions: list[tuple[int, int]] = []
        fake_user32 = _mock_user32(
            cursor_positions,
            {76: 0, 77: 0, 78: 1920, 79: 1080},
        )

        with (
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
            patch(
                "quasar_desktop_control._physical_dpi_context",
                return_value=_mock_dpi_context(fake_user32),
            ),
            patch.object(DesktopControlSession, "_user32", return_value=fake_user32),
        ):
            result = session.click(500, 500)

        self.assertEqual(cursor_positions, [(960, 540)])
        self.assertIn("캡처 좌표 (1280, 720)", result)
        self.assertIn("Windows 좌표 (960, 540)", result)

    def test_capture_uses_and_restores_per_monitor_dpi_context(self) -> None:
        contexts: list[int | None] = []

        def set_thread_context(context):
            contexts.append(context.value if isinstance(context, ctypes.c_void_p) else context)
            return ctypes.c_void_p(1) if len(contexts) == 1 else ctypes.c_void_p(2)

        fake_user32 = SimpleNamespace(
            SetThreadDpiAwarenessContext=set_thread_context,
        )
        image = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
        with (
            patch("quasar_desktop_control.sys.platform", "win32"),
            patch("quasar_desktop_control.ctypes.WinDLL", return_value=fake_user32),
            patch("quasar_desktop_control.ImageGrab.grab", return_value=image) as grab,
        ):
            captured = capture_desktop_image()

        self.assertEqual(captured.size, (1920, 1080))
        grab.assert_called_once_with(all_screens=True)
        self.assertEqual(contexts, [ctypes.c_void_p(-3).value, 1])

    def test_capture_frame_is_cleared_between_control_sessions(self) -> None:
        session = DesktopControlSession()
        session._capture_size = (1920, 1080)
        with (
            patch("quasar_desktop_control.sys.platform", "win32"),
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
        ):
            session.start()
        self.assertIsNone(session._capture_size)

    def test_unicode_text_uses_windows_input_structure(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        sent_batches: list[tuple[int, int, int]] = []

        def fake_send_input(count, inputs, input_size):
            sent_batches.append(
                (count, inputs[0].ki.wScan, input_size)
            )
            return count

        fake_user32 = SimpleNamespace(SendInput=fake_send_input)
        with (
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
            patch.object(DesktopControlSession, "_user32", return_value=fake_user32),
        ):
            result = session.type_text("한")

        self.assertIn("1자를 입력", result)
        self.assertEqual(len(sent_batches), 1)
        self.assertEqual(sent_batches[0], (2, 0xD55C, ctypes.sizeof(_Input)))

    def test_session_expires_after_its_deadline(self) -> None:
        session = DesktopControlSession(25)
        with (
            patch("quasar_desktop_control.sys.platform", "win32"),
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
        ):
            session.start()
        with patch("quasar_desktop_control.time.monotonic", return_value=100.0):
            self.assertEqual(session.remaining_seconds(), 25)
        with patch("quasar_desktop_control.time.monotonic", return_value=125.0):
            self.assertFalse(session.is_active())
            self.assertEqual(session.remaining_seconds(), 0)

    def test_session_duration_must_be_within_configured_limits(self) -> None:
        for duration in (9, 301, True):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, "10~300초"):
                    DesktopControlSession(duration)

    def test_changing_duration_does_not_extend_an_active_session(self) -> None:
        session = DesktopControlSession(20)
        with (
            patch("quasar_desktop_control.sys.platform", "win32"),
            patch("quasar_desktop_control.time.monotonic", return_value=100.0),
        ):
            session.start()
        session.set_duration_seconds(40)
        with patch("quasar_desktop_control.time.monotonic", return_value=110.0):
            self.assertEqual(session.remaining_seconds(), 10)

    def test_session_enforces_action_limit(self) -> None:
        session = DesktopControlSession()
        session._deadline = 500.0
        with patch("quasar_desktop_control.time.monotonic", return_value=100.0):
            for _ in range(MAX_ACTIONS_PER_SESSION):
                session._reserve_action()
            with self.assertRaisesRegex(RuntimeError, "제어 한도"):
                session._reserve_action()

    def test_text_input_rejects_long_or_control_text(self) -> None:
        session = DesktopControlSession()
        with self.assertRaisesRegex(ValueError, "200자"):
            session.type_text("x" * 201)
        with self.assertRaisesRegex(ValueError, "제어 문자"):
            session.type_text("first\nsecond")

    def test_image_encoding_returns_png_data_and_dimensions(self) -> None:
        image = Image.new("RGB", (3, 2), color=(1, 2, 3))

        encoded, width, height = encode_desktop_image(image)

        self.assertEqual((width, height), (3, 2))
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n"))

    def _recording_user32(
        self,
        cursor_positions: list[tuple[int, int]],
    ) -> tuple[SimpleNamespace, list[tuple[int, int]], list[list[tuple[int, int]]]]:
        metrics = {76: 0, 77: 0, 78: 1920, 79: 1080}
        mouse_calls: list[tuple[int, int]] = []
        send_calls: list[list[tuple[int, int]]] = []

        def set_cursor(x: int, y: int) -> bool:
            cursor_positions.append((x, y))
            return True

        def get_cursor(pointer: Any) -> bool:
            point = ctypes.cast(pointer, ctypes.POINTER(_Point)).contents
            point.x, point.y = cursor_positions[-1]
            return True

        def send_input(count: int, inputs: Any, _input_size: int) -> int:
            batch = [
                (inputs[index].ki.wVk, inputs[index].ki.dwFlags)
                for index in range(count)
            ]
            send_calls.append(batch)
            return count

        def mouse_event(flags: int, _dx: int, _dy: int, data: int, _extra: int) -> None:
            mouse_calls.append((flags, data))

        fake_user32 = SimpleNamespace(
            GetSystemMetrics=lambda index: metrics[index],
            SetPhysicalCursorPos=set_cursor,
            GetPhysicalCursorPos=get_cursor,
            mouse_event=mouse_event,
            SendInput=send_input,
        )
        return fake_user32, mouse_calls, send_calls

    @contextlib.contextmanager
    def _patched_session(
        self,
        fake_user32: SimpleNamespace,
        capture_size: tuple[int, int] = (1920, 1080),
    ):
        session = DesktopControlSession()
        session._deadline = 500.0
        session._capture_size = capture_size
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("quasar_desktop_control.time.monotonic", return_value=100.0)
            )
            stack.enter_context(patch("quasar_desktop_control.time.sleep"))
            stack.enter_context(
                patch(
                    "quasar_desktop_control._physical_dpi_context",
                    # return_value로는 같은 제너레이터가 재사용되므로 호출마다 새로 만든다.
                    side_effect=lambda: _mock_dpi_context(fake_user32),
                )
            )
            stack.enter_context(
                patch.object(
                    DesktopControlSession, "_user32", return_value=fake_user32
                )
            )
            yield session

    def test_press_key_accepts_modifiers_function_and_media_keys(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, _mouse_calls, send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            self.assertEqual(session.press_key("F5"), "F5 키를 입력했습니다.")
            self.assertEqual(session.press_key("CONTROL"), "CONTROL 키를 입력했습니다.")
            self.assertEqual(
                session.press_key("volume_up"),
                "VOLUME_UP 키를 입력했습니다.",
            )

        self.assertEqual(
            send_calls,
            [[(0x74, 0), (0x74, 0x0002)], [(0x11, 0), (0x11, 0x0002)], [(0xAF, 0), (0xAF, 0x0002)]],
        )

    def test_press_keys_sends_held_down_combination(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, _mouse_calls, send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            result = session.press_keys(["control", "c"])

        self.assertEqual(result, "CONTROL+C 키 조합을 입력했습니다.")
        # CONTROL down → C down → C up → CONTROL up (한 번의 SendInput)
        self.assertEqual(
            send_calls,
            [[(0x11, 0), (0x43, 0), (0x43, 0x0002), (0x11, 0x0002)]],
        )

    def test_press_keys_rejects_unknown_or_oversized_combinations(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, _mouse_calls, send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            with self.assertRaisesRegex(ValueError, "허용된 키"):
                session.press_keys(["CONTROL", "MISSING"])
            with self.assertRaisesRegex(ValueError, "최대 4개"):
                session.press_keys(["CONTROL", "SHIFT", "ALT", "C", "X"])
            with self.assertRaisesRegex(ValueError, "키 이름 목록"):
                session.press_keys("CONTROL+C")

        self.assertEqual(send_calls, [])

    def test_click_supports_right_middle_and_double_click(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            session.click(500, 500, "right")
            session.click(500, 500, "left", True)

        self.assertEqual(
            mouse_calls,
            [
                (0x0008, 0),
                (0x0010, 0),
                (0x0002, 0),
                (0x0004, 0),
                (0x0002, 0),
                (0x0004, 0),
            ],
        )

    def test_click_rejects_invalid_button_or_double_value(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            session._capture_size = (1920, 1080)
            with self.assertRaisesRegex(ValueError, "left, right, middle"):
                session.click(500, 500, "side")
            with self.assertRaisesRegex(ValueError, "double은"):
                session.click(500, 500, "left", "yes")

        self.assertEqual(mouse_calls, [])

    def test_scroll_sends_wheel_events_in_expected_direction(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            session.scroll("down", 5)
            session.scroll("RIGHT", 2, 250, 500)

        # 아래로 5클릭: WHEEL(0x0800) × -120
        self.assertEqual(mouse_calls[:5], [(0x0800, -120)] * 5)
        # 오른쪽 2클릭: 가로 휠(0x1000) × +120, 좌표 이동이 선행된다.
        self.assertEqual(mouse_calls[5:], [(0x1000, 120)] * 2)
        self.assertEqual(cursor_positions, [(480, 540)])

    def test_scroll_rejects_invalid_direction_clicks_or_partial_coordinates(
        self,
    ) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            session._capture_size = (1920, 1080)
            with self.assertRaisesRegex(ValueError, "up, down, left, right"):
                session.scroll("sideways")
            with self.assertRaisesRegex(ValueError, "1~10"):
                session.scroll("up", 11)
            with self.assertRaisesRegex(ValueError, "모두 입력"):
                session.scroll("up", 1, 500)

        self.assertEqual(mouse_calls, [])

    def test_drag_moves_through_interpolated_positions_and_releases(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )

        with self._patched_session(fake_user32) as session:
            result = session.drag(0, 0, 1000, 1000)

        self.assertIn("(0, 0)", result)
        self.assertIn("(1919, 1079)", result)
        # 처음 누르고 마지막에 놓는다.
        self.assertEqual(mouse_calls[0], (0x0002, 0))
        self.assertEqual(mouse_calls[-1], (0x0004, 0))
        # 시작 위치에서 끝 위치까지 균등하게 이동한다.
        self.assertEqual(cursor_positions[0], (0, 0))
        self.assertEqual(cursor_positions[-1], (1919, 1079))

    def test_drag_releases_the_button_even_when_cursor_moves_fail(self) -> None:
        cursor_positions: list[tuple[int, int]] = []
        fake_user32, mouse_calls, _send_calls = self._recording_user32(
            cursor_positions
        )
        set_cursor = fake_user32.SetPhysicalCursorPos
        calls_before_failure = {"count": 0}

        def failing_set_cursor(x: int, y: int) -> bool:
            calls_before_failure["count"] += 1
            if calls_before_failure["count"] > 3:
                return False
            return set_cursor(x, y)

        fake_user32.SetPhysicalCursorPos = failing_set_cursor

        with self._patched_session(fake_user32) as session:
            with self.assertRaisesRegex(OSError, "마우스를 이동하지 못했습니다"):
                session.drag(0, 0, 1000, 1000)

        # 중간에 실패해도 마우스 버튼을 반드시 놓는다.
        self.assertEqual(mouse_calls[-1], (0x0004, 0))


def _tool_call(name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _tool_response(*calls: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=list(calls),
                    model_dump=lambda **_kwargs: {
                        "role": "assistant",
                        "tool_calls": [],
                    },
                )
            )
        ]
    )


class DesktopCaptureAgentTests(unittest.TestCase):
    def test_capture_image_is_sent_to_model_before_desktop_action(self) -> None:
        captured_screen = ("cG5n", 800, 600)
        responses = iter(
            [
                _tool_response(
                    _tool_call("desktop_capture", "{}"),
                    _tool_call("desktop_click", '{"x":120,"y":240}'),
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="화면을 확인했습니다.",
                                tool_calls=[],
                            )
                        )
                    ]
                ),
            ]
        )

        class FakeCompletions:
            def __init__(self) -> None:
                self.messages = []

            def create(self, **kwargs):
                self.messages.append(kwargs["messages"])
                return next(responses)

        completions = FakeCompletions()
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=completions,
            )
        )
        messages = [{"role": "system", "content": "system"}]

        with (
            patch.object(quasar_cli, "is_responses_model", return_value=False),
            patch.object(quasar_cli, "is_screen_request", return_value=False),
            patch.object(
                quasar_cli,
                "run_tool",
                return_value=captured_screen,
            ) as run_tool,
            patch("sys.stdout"),
        ):
            answer = quasar_cli.request_agent(
                client,
                "vision-model",
                messages,
                "이 화면을 확인해",
                tool_calling_mode="legacy",
            )

        self.assertEqual(answer, "화면을 확인했습니다.")
        run_tool.assert_called_once()
        second_request = completions.messages[1]
        screenshot_message = next(
            message
            for message in second_request
            if message.get("role") == "user"
            and isinstance(message.get("content"), list)
        )
        self.assertIn("0~1000으로 정규화", screenshot_message["content"][0]["text"])
        self.assertEqual(
            screenshot_message["content"][1]["image_url"]["url"],
            "data:image/png;base64,cG5n",
        )


if __name__ == "__main__":
    unittest.main()
