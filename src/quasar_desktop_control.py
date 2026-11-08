from __future__ import annotations

import base64
import ctypes
import io
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from PIL import Image, ImageGrab


DEFAULT_SESSION_SECONDS = 60
MIN_SESSION_SECONDS = 10
MAX_SESSION_SECONDS = 300
MAX_ACTIONS_PER_SESSION = 40
MAX_TYPED_CHARACTERS = 200
NORMALIZED_COORDINATE_MAX = 1000
MAX_COMBO_KEYS = 4
MAX_SCROLL_CLICKS = 10
DRAG_INTERPOLATION_STEPS = 12
DRAG_STEP_SLEEP_SECONDS = 0.01
DOUBLE_CLICK_SLEEP_SECONDS = 0.02
WHEEL_DELTA = 120

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

MOUSE_BUTTON_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}

MOUSE_BUTTON_LABELS = {
    "left": "왼쪽",
    "right": "오른쪽",
    "middle": "가운데",
}

_SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})

_ALLOWED_KEYS = {
    "ALT": 0x12,
    "APPS": 0x5D,
    "BACKSPACE": 0x08,
    "BROWSER_BACK": 0xA6,
    "BROWSER_FORWARD": 0xA7,
    "CONTROL": 0x11,
    "DELETE": 0x2E,
    "DOWN": 0x28,
    "END": 0x23,
    "ENTER": 0x0D,
    "ESCAPE": 0x1B,
    "F1": 0x70,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "HOME": 0x24,
    "INSERT": 0x2D,
    "LEFT": 0x25,
    "LWIN": 0x5B,
    "MEDIA_NEXT_TRACK": 0xB0,
    "MEDIA_PLAY_PAUSE": 0xB3,
    "MEDIA_PREV_TRACK": 0xB1,
    "NUMLOCK": 0x90,
    "PAGEDOWN": 0x22,
    "PAGEUP": 0x21,
    "PRINTSCREEN": 0x2C,
    "RIGHT": 0x27,
    "SCROLLLOCK": 0x91,
    "SHIFT": 0x10,
    "SPACE": 0x20,
    "TAB": 0x09,
    "UP": 0x26,
    "VOLUME_DOWN": 0xAE,
    "VOLUME_MUTE": 0xAD,
    "VOLUME_UP": 0xAF,
}

# 키 조합(CTRL+C, ALT+F4, LWIN+E 등)에 쓸 알파벳·숫자 키를 추가한다.
for _letter_code in range(0x41, 0x5B):  # A-Z
    _ALLOWED_KEYS[chr(_letter_code)] = _letter_code
for _digit_code in range(0x30, 0x3A):  # 0-9
    _ALLOWED_KEYS[chr(_digit_code)] = _digit_code

# 도구 스키마 등 외부에서 허용 키 목록을 참조할 때 쓰는 공개 별칭이다.
ALLOWED_DESKTOP_KEYS = frozenset(_ALLOWED_KEYS)


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyboardInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", ctypes.c_uint), ("union", _InputUnion)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _windows_api_error(message: str) -> OSError:
    error_code = ctypes.get_last_error()
    return OSError(error_code, f"{message}: {ctypes.FormatError(error_code)}")


@contextmanager
def _physical_dpi_context() -> Iterator[Any]:
    if sys.platform != "win32":
        raise RuntimeError("데스크톱 화면 캡처는 Windows에서만 지원합니다.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_thread_context = user32.SetThreadDpiAwarenessContext
    except AttributeError as error:
        raise RuntimeError(
            "Per-Monitor DPI 좌표 모드를 지원하는 Windows가 필요합니다."
        ) from error
    set_thread_context.argtypes = [ctypes.c_void_p]
    set_thread_context.restype = ctypes.c_void_p
    previous_context = set_thread_context(ctypes.c_void_p(-3))
    if not previous_context:
        raise _windows_api_error(
            "화면 좌표 DPI 모드를 설정하지 못했습니다"
        )
    try:
        yield user32
    finally:
        if not set_thread_context(previous_context):
            raise _windows_api_error(
                "화면 좌표 DPI 모드를 복구하지 못했습니다"
            )


def capture_desktop_image() -> Image.Image:
    with _physical_dpi_context():
        return ImageGrab.grab(all_screens=True).convert("RGB")


def encode_desktop_image(image: Image.Image) -> tuple[str, int, int]:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return (
        base64.b64encode(buffer.getvalue()).decode("ascii"),
        image.width,
        image.height,
    )


class DesktopControlSession:
    def __init__(
        self,
        duration_seconds: int = DEFAULT_SESSION_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._actions = 0
        self._capture_size: tuple[int, int] | None = None
        self._duration_seconds = DEFAULT_SESSION_SECONDS
        self.set_duration_seconds(duration_seconds)

    @property
    def duration_seconds(self) -> int:
        with self._lock:
            return self._duration_seconds

    def set_duration_seconds(self, duration_seconds: int) -> None:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or not MIN_SESSION_SECONDS
            <= duration_seconds
            <= MAX_SESSION_SECONDS
        ):
            raise ValueError(
                f"화면 제어 시간은 {MIN_SESSION_SECONDS}~"
                f"{MAX_SESSION_SECONDS}초 사이여야 합니다."
            )
        with self._lock:
            self._duration_seconds = duration_seconds

    def start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("데스크톱 입력 제어는 Windows에서만 지원합니다.")
        with self._lock:
            self._deadline = time.monotonic() + self._duration_seconds
            self._actions = 0
            self._capture_size = None

    def stop(self) -> None:
        with self._lock:
            self._deadline = None
            self._capture_size = None

    def remaining_seconds(self) -> int:
        with self._lock:
            if self._deadline is None:
                return 0
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._deadline = None
                return 0
            return int(remaining + 0.999)

    def is_active(self) -> bool:
        return self.remaining_seconds() > 0

    def capture_for_agent(self) -> tuple[str, int, int]:
        if not self.is_active():
            raise RuntimeError(
                "데스크톱 제어 세션이 활성화되어 있지 않거나 만료되었습니다."
            )
        image = capture_desktop_image()
        with self._lock:
            if self._deadline is None or time.monotonic() >= self._deadline:
                self._deadline = None
                raise RuntimeError(
                    "데스크톱 제어 세션이 활성화되어 있지 않거나 만료되었습니다."
                )
            self._capture_size = image.size
        return encode_desktop_image(image)

    def move_mouse(self, x: Any, y: Any) -> str:
        self._reserve_action()
        x, y, screen_x, screen_y, origin_x, origin_y = self._screen_coordinates(
            x,
            y,
        )
        user32 = self._user32()
        self._set_physical_cursor(
            user32,
            origin_x + screen_x,
            origin_y + screen_y,
        )
        return (
            f"캡처 좌표 ({x}, {y})에서 Windows 좌표 "
            f"({screen_x}, {screen_y})로 마우스를 이동했습니다."
        )

    def click(
        self,
        x: Any,
        y: Any,
        button: Any = "left",
        double: Any = False,
    ) -> str:
        if not isinstance(button, str) or button.lower() not in MOUSE_BUTTON_FLAGS:
            raise ValueError(
                "클릭 버튼은 left, right, middle 중 하나여야 합니다."
            )
        if not isinstance(double, bool):
            raise ValueError("double은 참/거짓 값이어야 합니다.")
        self._reserve_action()
        x, y, screen_x, screen_y, origin_x, origin_y = self._screen_coordinates(
            x,
            y,
        )
        user32 = self._user32()
        self._set_physical_cursor(
            user32,
            origin_x + screen_x,
            origin_y + screen_y,
        )
        button_name = button.lower()
        down_flag, up_flag = MOUSE_BUTTON_FLAGS[button_name]
        click_count = 2 if double else 1
        for click_index in range(click_count):
            if click_index:
                time.sleep(DOUBLE_CLICK_SLEEP_SECONDS)
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
        label = MOUSE_BUTTON_LABELS[button_name]
        action = "더블클릭" if double else "클릭"
        return (
            f"캡처 좌표 ({x}, {y}), Windows 좌표 ({screen_x}, {screen_y})를 "
            f"{label} {action}했습니다."
        )

    def scroll(
        self,
        direction: Any,
        clicks: Any = 3,
        x: Any = None,
        y: Any = None,
    ) -> str:
        if (
            not isinstance(direction, str)
            or direction.lower() not in _SCROLL_DIRECTIONS
        ):
            raise ValueError(
                "스크롤 방향은 up, down, left, right 중 하나여야 합니다."
            )
        if (
            isinstance(clicks, bool)
            or not isinstance(clicks, int)
            or not 1 <= clicks <= MAX_SCROLL_CLICKS
        ):
            raise ValueError(
                f"스크롤 클릭 수는 1~{MAX_SCROLL_CLICKS} 사이의 정수여야 합니다."
            )
        should_move = x is not None or y is not None
        if should_move and (x is None or y is None):
            raise ValueError(
                "스크롤 위치를 지정할 때는 x와 y를 모두 입력해야 합니다."
            )
        self._reserve_action()
        user32 = self._user32()
        if should_move:
            _, _, screen_x, screen_y, origin_x, origin_y = self._screen_coordinates(
                x,
                y,
            )
            self._set_physical_cursor(
                user32,
                origin_x + screen_x,
                origin_y + screen_y,
            )
        direction_name = direction.lower()
        vertical = direction_name in {"up", "down"}
        event_flag = MOUSEEVENTF_WHEEL if vertical else MOUSEEVENTF_HWHEEL
        delta = (
            WHEEL_DELTA
            if direction_name in {"up", "right"}
            else -WHEEL_DELTA
        )
        for _ in range(clicks):
            user32.mouse_event(event_flag, 0, 0, delta, 0)
        return f"마우스 휠로 {direction_name} 방향으로 {clicks}클릭 스크롤했습니다."

    def drag(self, x1: Any, y1: Any, x2: Any, y2: Any) -> str:
        self._reserve_action()
        start_x, start_y, screen_x1, screen_y1, origin_x, origin_y = (
            self._screen_coordinates(x1, y1)
        )
        end_x, end_y, screen_x2, screen_y2, _, _ = self._screen_coordinates(
            x2,
            y2,
        )
        user32 = self._user32()
        self._set_physical_cursor(
            user32,
            origin_x + screen_x1,
            origin_y + screen_y1,
        )
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            for step in range(1, DRAG_INTERPOLATION_STEPS + 1):
                ratio = step / DRAG_INTERPOLATION_STEPS
                step_x = round(screen_x1 + (screen_x2 - screen_x1) * ratio)
                step_y = round(screen_y1 + (screen_y2 - screen_y1) * ratio)
                self._set_physical_cursor(
                    user32,
                    origin_x + step_x,
                    origin_y + step_y,
                )
                time.sleep(DRAG_STEP_SLEEP_SECONDS)
        finally:
            # 커서 검증이 중간에 실패해도 마우스 버튼은 반드시 놓는다.
            user32.mouse_event(0x0004, 0, 0, 0, 0)
        return (
            f"캡처 좌표 ({start_x}, {start_y})에서 ({end_x}, {end_y})까지 "
            "마우스 왼쪽 버튼을 누른 채 이동해 놓았습니다."
        )

    def type_text(self, text: Any) -> str:
        if not isinstance(text, str) or not text:
            raise ValueError("입력할 문자를 지정해야 합니다.")
        if len(text) > MAX_TYPED_CHARACTERS:
            raise ValueError(
                f"한 번에 입력할 수 있는 문자는 {MAX_TYPED_CHARACTERS}자 이하입니다."
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise ValueError("문자 입력에는 제어 문자를 포함할 수 없습니다.")
        self._reserve_action()

        user32 = self._user32()
        keyeventf_unicode = 0x0004
        keyeventf_keyup = 0x0002
        encoded = text.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset : offset + 2], "little")
            inputs = (_Input * 2)()
            inputs[0].type = 1
            inputs[0].ki = _KeyboardInput(0, unit, keyeventf_unicode, 0, 0)
            inputs[1].type = 1
            inputs[1].ki = _KeyboardInput(
                0,
                unit,
                keyeventf_unicode | keyeventf_keyup,
                0,
                0,
            )
            sent = user32.SendInput(2, inputs, ctypes.sizeof(_Input))
            if sent != 2:
                raise self._windows_error("문자 입력을 완료하지 못했습니다")
        return f"문자 {len(text)}자를 입력했습니다."

    def press_key(self, key: Any) -> str:
        if not isinstance(key, str):
            raise ValueError("허용된 키 이름을 입력해야 합니다.")
        normalized_key = key.strip().upper()
        virtual_key = _ALLOWED_KEYS.get(normalized_key)
        if virtual_key is None:
            raise ValueError(
                "허용된 키: " + ", ".join(sorted(_ALLOWED_KEYS))
            )
        self._reserve_action()

        user32 = self._user32()
        inputs = (_Input * 2)()
        inputs[0].type = 1
        inputs[0].ki = _KeyboardInput(virtual_key, 0, 0, 0, 0)
        inputs[1].type = 1
        inputs[1].ki = _KeyboardInput(virtual_key, 0, 0x0002, 0, 0)
        sent = user32.SendInput(2, inputs, ctypes.sizeof(_Input))
        if sent != 2:
            raise self._windows_error(f"{normalized_key} 키 입력을 완료하지 못했습니다")
        return f"{normalized_key} 키를 입력했습니다."

    def press_keys(self, keys: Any) -> str:
        """여러 키를 동시에 누르는 조합 입력(예: CTRL+C, ALT+TAB)을 처리한다."""
        if not isinstance(keys, (list, tuple)):
            raise ValueError('키 조합은 키 이름 목록이어야 합니다. 예: ["CONTROL", "C"]')
        normalized_keys: list[str] = []
        virtual_keys: list[int] = []
        for key in keys:
            if not isinstance(key, str):
                raise ValueError("키 이름은 문자열이어야 합니다.")
            normalized_key = key.strip().upper()
            virtual_key = _ALLOWED_KEYS.get(normalized_key)
            if virtual_key is None:
                raise ValueError(
                    "허용된 키: " + ", ".join(sorted(_ALLOWED_KEYS))
                )
            if virtual_key not in virtual_keys:
                normalized_keys.append(normalized_key)
                virtual_keys.append(virtual_key)
        if not virtual_keys:
            raise ValueError("누를 키를 지정해야 합니다.")
        if len(virtual_keys) > MAX_COMBO_KEYS:
            raise ValueError(
                f"키 조합은 최대 {MAX_COMBO_KEYS}개까지 지원합니다."
            )
        self._reserve_action()

        user32 = self._user32()
        keyup_flag = 0x0002
        inputs = (_Input * (2 * len(virtual_keys)))()
        for index, virtual_key in enumerate(virtual_keys):
            inputs[index].type = 1
            inputs[index].ki = _KeyboardInput(virtual_key, 0, 0, 0, 0)
            inputs[len(virtual_keys) + index].type = 1
            inputs[len(virtual_keys) + index].ki = _KeyboardInput(
                virtual_keys[-1 - index],
                0,
                keyup_flag,
                0,
                0,
            )
        sent = user32.SendInput(len(inputs), inputs, ctypes.sizeof(_Input))
        if sent != len(inputs):
            raise self._windows_error("키 조합 입력을 완료하지 못했습니다")
        return f"{'+'.join(normalized_keys)} 키 조합을 입력했습니다."

    def _reserve_action(self) -> None:
        with self._lock:
            if self._deadline is None or time.monotonic() >= self._deadline:
                self._deadline = None
                raise RuntimeError(
                    "데스크톱 제어 세션이 활성화되어 있지 않거나 만료되었습니다."
                )
            if self._actions >= MAX_ACTIONS_PER_SESSION:
                raise RuntimeError(
                    f"세션당 제어 한도({MAX_ACTIONS_PER_SESSION}회)를 초과했습니다."
                )
            self._actions += 1

    def _screen_coordinates(
        self,
        x: Any,
        y: Any,
    ) -> tuple[int, int, int, int, int, int]:
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            raise ValueError("정규화 좌표 x와 y는 정수여야 합니다.")

        with self._lock:
            capture_size = self._capture_size
        if capture_size is None:
            raise RuntimeError(
                "먼저 desktop_capture로 현재 화면을 캡처한 뒤 좌표를 지정하세요."
            )

        with _physical_dpi_context() as user32:
            origin_x = user32.GetSystemMetrics(76)
            origin_y = user32.GetSystemMetrics(77)
            width = user32.GetSystemMetrics(78)
            height = user32.GetSystemMetrics(79)
        if width < 1 or height < 1:
            raise RuntimeError("가상 화면 크기를 확인하지 못했습니다.")
        image_width, image_height = capture_size
        if image_width < 1 or image_height < 1:
            raise RuntimeError("화면 캡처 크기를 확인하지 못했습니다.")
        if (
            not 0 <= x <= NORMALIZED_COORDINATE_MAX
            or not 0 <= y <= NORMALIZED_COORDINATE_MAX
        ):
            raise ValueError(
                "정규화 좌표 범위를 벗어났습니다. 허용 범위: "
                f"x=0..{NORMALIZED_COORDINATE_MAX}, "
                f"y=0..{NORMALIZED_COORDINATE_MAX}"
            )
        image_x = round(x * (image_width - 1) / NORMALIZED_COORDINATE_MAX)
        image_y = round(y * (image_height - 1) / NORMALIZED_COORDINATE_MAX)
        screen_x = round(image_x * (width - 1) / max(1, image_width - 1))
        screen_y = round(image_y * (height - 1) / max(1, image_height - 1))
        return image_x, image_y, screen_x, screen_y, origin_x, origin_y

    @classmethod
    def _set_physical_cursor(cls, user32: Any, x: int, y: int) -> None:
        if not user32.SetPhysicalCursorPos(x, y):
            raise cls._windows_error("마우스를 이동하지 못했습니다")
        position = _Point()
        if not user32.GetPhysicalCursorPos(ctypes.byref(position)):
            raise cls._windows_error("마우스 위치를 확인하지 못했습니다")
        if (position.x, position.y) != (x, y):
            raise RuntimeError(
                f"마우스 위치 검증 불일치: 요청 ({x}, {y}), "
                f"실제 ({position.x}, {position.y})"
            )

    @staticmethod
    def _user32() -> Any:
        if sys.platform != "win32":
            raise RuntimeError("데스크톱 입력 제어는 Windows에서만 지원합니다.")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetPhysicalCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetPhysicalCursorPos.restype = ctypes.c_bool
        user32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(_Point)]
        user32.GetPhysicalCursorPos.restype = ctypes.c_bool
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.mouse_event.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_size_t,
        ]
        user32.SendInput.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        user32.SendInput.restype = ctypes.c_uint
        return user32

    @staticmethod
    def _windows_error(message: str) -> OSError:
        return _windows_api_error(message)
