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

_ALLOWED_KEYS = {
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DOWN": 0x28,
    "END": 0x23,
    "ENTER": 0x0D,
    "ESCAPE": 0x1B,
    "HOME": 0x24,
    "LEFT": 0x25,
    "PAGEDOWN": 0x22,
    "PAGEUP": 0x21,
    "RIGHT": 0x27,
    "SPACE": 0x20,
    "TAB": 0x09,
    "UP": 0x26,
}


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

    def click(self, x: Any, y: Any) -> str:
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
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        return (
            f"캡처 좌표 ({x}, {y}), Windows 좌표 ({screen_x}, {screen_y})를 "
            "왼쪽 클릭했습니다."
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
