from __future__ import annotations

import base64
import contextlib
import ctypes
import csv
import gc
import io
import json
import math
import os
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen

import quasar_cli as agent
from chat_history import ChatHistoryStore
from PySide6.QtCore import QObject, QPointF, QSettings, QStandardPaths, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from quasar_galaxy import (
    CANVAS_BACKGROUND_STOPS,
    BlackHoleOverlay,
    GalaxyTransitionOverlay,
    OrbWidget,
)
from quasar_desktop_control import (
    MAX_ACTIONS_PER_SESSION,
    MAX_SESSION_SECONDS,
    DEFAULT_SESSION_SECONDS,
    MIN_SESSION_SECONDS,
    ALLOWED_DESKTOP_KEYS,
    DesktopControlSession,
    capture_desktop_image,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


BACKGROUND = "#070a16"
TEXT = "#e7eaff"
MUTED = "#929bb8"
ACCENT = "#9b91ff"
WINDOWS_APP_USER_MODEL_ID = "readonlymemory1.Quasar"

SYSTEM_PROMPT_BASE = (
    "You are Quasar, the assistant in the Quasar desktop app. Reply concisely and accurately in the "
    "user's language, especially Korean or English. "
    "TOOL AVAILABILITY AND SCOPE: Use only tools listed for the current request. Do not assume a tool "
    "from another app, client, or MCP server is available. If an integration is needed and the current "
    "tools provide MCP discovery, discover it first and use only the returned tool names and schemas. "
    "Follow each tool's own description and arguments. Instructions for one tool or integration apply "
    "only to that tool; do not transfer its workflow, parameters, or guarantees to another tool. "
    "If a required tool is unavailable, say so instead of inventing or substituting a tool. "
    "STATUS AND COMPLETION: Whenever the request needs tools, first send one message_to_user that briefly "
    "states what you understood and are about to do in a short sentence (e.g. '크롬 브라우저를 "
    "시작하겠습니다.'), then start the work. For multi-step work, continue with brief progress updates "
    "after meaningful phases and before another lengthy wait. Use message_to_user's error kind only when "
    "reporting an observed blocker and the recovery being attempted. Do not narrate every tool call; "
    "combine quick related actions. Verify results before claiming completion. A command tool's result "
    "includes its exit code; inspect it before reporting success or starting a dependent command. "
    "MCP DIAGNOSTICS: Do not infer that Node.js or npx is missing from an MCP error alone. For an MCP "
    "launch failure, inspect the reported server status and configured command. Before recommending "
    "Node.js installation, test node --version and npx --version in the same environment used by the "
    "command tool, if that tool is available. A WinError 2 means the configured executable could not be "
    "resolved for that launch; it does not prove Node.js is absent or that PATH is stale. Do not suggest "
    "restarting the app or IDE as a generic remedy. Report confirmed facts separately from possibilities, "
    "then verify any recovery. "
    "SAFETY: Use tools only for requested work. Run requested low-risk commands without extra "
    "confirmation. Get explicit approval before destructive actions, system-setting changes, or "
    "irreversible external actions such as submitting, deleting, purchasing, or changing an account. "
    "Never enter passwords or secrets or bypass security prompts. Send email only when explicitly "
    "requested; clarify ambiguous sending requests first. "
    "BROWSER TOOLS: For browser tasks, navigate to the requested page once and continue on the current "
    "page. Do not revisit or reload the same URL unless asked. Before interacting, inspect the page DOM; "
    "if it is insufficient to identify or understand the target, inspect a screenshot. Reinspect the DOM "
    "after navigation or a meaningful page-state change. Discover browser tools only through the available "
    "MCP discovery tool, using an English capability query when needed. These rules apply only to browser "
    "tools and do not describe desktop controls. "
    "POWERPOINT: For presentation requests, use connected PowerPoint MCP tools only if discovery makes "
    "them available. Do not claim the integration is unavailable before checking, or claim success before "
    "verifying its result."
)

DESKTOP_CONTROL_SYSTEM_PROMPT = (
    "DESKTOP CONTROLS: Use desktop-control tools only during a user-started, time-limited desktop "
    "session, and only as a last resort. First try non-desktop alternatives such as browser tools, "
    "command tools, or file tools; reach for desktop control only when those cannot do the job and "
    "have failed, or when the user explicitly asks you to move the mouse or control the screen. Do "
    "not propose desktop control as the first option. Before the first desktop tool, and whenever a "
    "desktop action reports an inactive or expired session, call desktop_start_session to ask the "
    "user for approval and then retry; never report a task as impossible merely because the session "
    "was off. Their mouse coordinates are normalized from 0 to 1000 against the original screenshot, "
    "not pixels or a resized preview. These rules apply only to desktop-control tools. "
)


def build_system_prompt(desktop_enabled: bool) -> str:
    """데스크톱 제어가 켜진 경우에만 관련 지침을 시스템 프롬프트에 포함한다."""
    if desktop_enabled:
        return SYSTEM_PROMPT_BASE + DESKTOP_CONTROL_SYSTEM_PROMPT
    return SYSTEM_PROMPT_BASE


# 하위 호환용 기본값. 앱에서는 build_system_prompt()로 설정 상태를 반영한다.
SYSTEM_PROMPT = build_system_prompt(False)

NEMO_SPEECH_MODEL = "nemotron-3.5"
SPEECH_OUTPUT_GAIN = 3.0
VOICE_RECOGNITION_SAMPLE_RATE = 16_000
VOICE_FALLBACK_SAMPLE_RATES = (48_000, 44_100, 16_000)
VOICE_LANGUAGE = "ko"
VOICE_INITIAL_PROMPT = "다음은 한국어로 말한 일상적인 음성입니다. 뜬금없는 단어나 다른 언어를 넣지 말고 들리는 그대로 한국어로 적으세요."
VOICE_INPUT_MAX_GAIN = 4.0
VOICE_INPUT_TARGET_PEAK = 0.65
VOICE_CONVERSATION_END_SILENCE_SECONDS = 0.6
TRANSCRIPT_CORRECTION_TIMEOUT_SECONDS = 12.0
VOICE_RESPONSE_SYSTEM_PROMPT = (
    "[음성 대화 모드 규칙] 지금 답변은 소리 내어 읽어 준다. 다음을 반드시 지켜라.\n"
    "1. 도구 실행, 화면 제어, 파일 작업 등 동작을 시작하기 전에 "
    "'크롬 브라우저를 시작하겠습니다.'처럼 한 문장으로 먼저 짧게 알린다.\n"
    "2. 답변은 1~3문장으로 간결하게. 장황한 설명이나 확인 질문을 덧붙이지 않는다.\n"
    "3. URL 주소, 링크, 파일 경로, 코드, 표, 나열 기호를 말하지 않는다. "
    "링크가 필요하면 '링크를 보내드릴게요' 정도로만 말한다.\n"
    "4. 이모지와 마크다운 기호를 쓰지 않고, 말하기 좋은 자연스러운 문장으로만 답한다."
)
TRANSCRIPT_CORRECTION_SYSTEM_PROMPT = (
    "너는 음성인식(ASR) 문장 교정기다. 입력은 마이크로 말한 한국어 문장을 로컬 "
    "음성인식 모델이 잘못 기록한 결과다. 소리가 비슷한 올바른 단어로만 바꾸어 "
    "사용자가 말한 원래 문장을 복원하라.\n"
    "규칙:\n"
    "1. 소리가 비슷한 단어만 고친다. 예: '크록 브라우드 켜봐'은 '크롬 브라우저 켜봐'이다.\n"
    "2. 문맥상 원래 무슨 말이었는지 확실하지 않으면 그 부분은 고치지 않고 그대로 둔다.\n"
    "3. 새로운 단어를 추가하거나 문장을 다시 쓰지 않는다.\n"
    "4. 설명하지 말고 교정된 문장 딱 한 줄만 출력한다.\n"
    "5. 입력에 고칠 오류가 없으면 입력을 그대로 출력한다."
)

VOICE_ENGINE_NEMO = "nemo"
VOICE_ENGINE_WHISPER = "whisper"
VOICE_ENGINE_NIM = "nim"
VOICE_ENGINE_OMNI = "omni"
VOICE_ENGINE_LABELS = {
    VOICE_ENGINE_NEMO: "NeMo (nemotron-3.5 로컬 서버)",
    VOICE_ENGINE_WHISPER: "Whisper (faster-whisper 로컬 모델)",
    VOICE_ENGINE_NIM: "NVIDIA NIM (whisper-large-v3 클라우드·무료 체험)",
    VOICE_ENGINE_OMNI: "Omni (omni 모델이 음성을 직접 인식)",
}
# omni 엔진은 음성 파일을 모델에 직접 첨부하므로 화면에 보일 전사 문장 대신
# 이 자리표시자가 입력·기록에 사용된다.
VOICE_OMNI_PLACEHOLDER = "(음성 질문)"
VOICE_NIM_SERVER = "grpc.nvcf.nvidia.com:443"
VOICE_NIM_FUNCTION_ID = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
VOICE_NIM_API_KEY_ENV = "NVIDIA_API_KEY"
WHISPER_DEFAULT_MODEL = "small"
WHISPER_MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v3")
WHISPER_SETTINGS_PREFIX = "speech"


class _OfficeDocumentSource:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def latest_document(self) -> None:
        return None


class AppSignals(QObject):
    response_ready = Signal(str, object)
    request_cancelled = Signal()
    request_failed = Signal(str)
    request_progress = Signal(str)
    agent_message = Signal(str, str)
    alarm_triggered = Signal(str)
    models_loaded = Signal(str, object, object)
    voice_status = Signal(str)
    voice_text_ready = Signal(str)
    voice_text_final = Signal(str)
    voice_audio_ready = Signal(str)
    voice_failed = Signal(str)
    conversation_voice_text_ready = Signal(str)
    conversation_voice_text_final = Signal(str)
    conversation_voice_failed = Signal(str)
    speech_finished = Signal(object)
    speech_failed = Signal(object, str)
    tts_enabled_requested = Signal(bool)
    tool_approval_requested = Signal(str, str, object)
    audio_test_ready = Signal(object)
    audio_test_failed = Signal(str)
    audio_test_playback_finished = Signal()
    audio_test_playback_failed = Signal(str)


class VoicePushButton(QPushButton):
    hold_started = Signal()
    hold_ended = Signal()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.hold_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.hold_ended.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self.hold_started.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self.hold_ended.emit()
            event.accept()
            return
        super().keyReleaseEvent(event)


class RadialMenuOverlay(QWidget):
    dismissed = Signal()

    def mousePressEvent(self, event: Any) -> None:
        self.dismissed.emit()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismissed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class RadialStarburstOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.progress = 0.0
        self.origin = QPointF()
        self.targets: list[QPointF] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_animation_state(
        self,
        progress: float,
        origin: QPointF,
        targets: list[QPointF],
    ) -> None:
        self.progress = max(0.0, min(1.0, progress))
        self.origin = origin
        self.targets = targets
        self.update()

    def paintEvent(self, _event: Any) -> None:
        if not self.targets or self.progress <= 0.0 or self.progress >= 1.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, target in enumerate(self.targets):
            delay = (index % 4) * 0.055
            progress = max(0.0, min(1.0, (self.progress - delay) / (1 - delay)))
            if progress <= 0.0 or progress >= 1.0:
                continue
            for particle_index in range(3):
                offset = (particle_index - 1) * 0.11
                particle_progress = max(
                    0.0,
                    min(1.0, progress - offset),
                )
                particle_eased = particle_progress * particle_progress * (
                    3 - 2 * particle_progress
                )
                point = QPointF(
                    self.origin.x()
                    + (target.x() - self.origin.x()) * particle_eased,
                    self.origin.y()
                    + (target.y() - self.origin.y()) * particle_eased,
                )
                alpha = int(235 * (1 - abs(particle_progress - 0.52) * 1.6))
                if alpha <= 0:
                    continue
                color = QColor(176, 203, 255, min(255, alpha))
                painter.setPen(QPen(color, 1.2, Qt.PenStyle.SolidLine))
                radius = 2.2 if particle_index == 1 else 1.35
                painter.drawLine(
                    QPointF(point.x() - radius, point.y()),
                    QPointF(point.x() + radius, point.y()),
                )
                painter.drawLine(
                    QPointF(point.x(), point.y() - radius),
                    QPointF(point.x(), point.y() + radius),
                )

        galaxy_alpha = int(215 * (1 - self.progress))
        if galaxy_alpha > 0:
            painter.setPen(QPen(QColor(160, 173, 255, galaxy_alpha), 1.4))
            painter.drawEllipse(
                self.origin,
                13 + 15 * self.progress,
                5 + 6 * self.progress,
            )
            painter.setPen(QPen(QColor(226, 232, 255, galaxy_alpha), 2.0))
            painter.drawPoint(self.origin)


class MicrophoneSettingsDialog(QDialog):
    def __init__(
        self,
        selected_device_index: int | None,
        max_tool_rounds: int,
        tool_calling_mode: str,
        desktop_control_seconds: int,
        recognition_engine: str = VOICE_ENGINE_NEMO,
        whisper_model: str = WHISPER_DEFAULT_MODEL,
        transcript_correction_enabled: bool = False,
        nim_api_key: str = "",
        desktop_control_enabled: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.microphone_selector = QComboBox()
        form.addRow("입력 마이크", self.microphone_selector)
        self.engine_selector = QComboBox()
        for engine in (
            VOICE_ENGINE_NEMO,
            VOICE_ENGINE_WHISPER,
            VOICE_ENGINE_NIM,
            VOICE_ENGINE_OMNI,
        ):
            self.engine_selector.addItem(VOICE_ENGINE_LABELS[engine], engine)
        engine_index = self.engine_selector.findData(
            normalize_voice_engine(recognition_engine)
        )
        self.engine_selector.setCurrentIndex(max(0, engine_index))
        self.engine_selector.setToolTip(
            "음성 입력을 로컬에서 인식할 엔진을 선택합니다."
        )
        form.addRow("음성 인식 엔진", self.engine_selector)
        self.whisper_model_selector = QComboBox()
        for model_name in WHISPER_MODEL_CHOICES:
            self.whisper_model_selector.addItem(model_name, model_name)
        model_index = self.whisper_model_selector.findData(whisper_model)
        self.whisper_model_selector.setCurrentIndex(max(0, model_index))
        self.whisper_model_selector.setToolTip(
            "Whisper 모델 크기. 작을수록 빠르고, 클수록 정확합니다."
        )
        form.addRow("Whisper 모델", self.whisper_model_selector)
        self.nim_api_key_selector = QLineEdit()
        self.nim_api_key_selector.setEchoMode(QLineEdit.EchoMode.Password)
        self.nim_api_key_selector.setPlaceholderText(
            "build.nvidia.com 무료 API 키 (nvapi-...)"
        )
        self.nim_api_key_selector.setToolTip(
            "http://build.nvidia.com 에서 무료로 발급한 API 키를 붙여 넣으세요. "
            "whisper-large-v3 클라우드 모델로, 가장 정확한 한국어 인식을 제공합니다."
        )
        form.addRow("NVIDIA API 키", self.nim_api_key_selector)
        self.transcript_correction_selector = QCheckBox(
            "AI로 음성 인식 문장 교정 (한 번 더 모델로 고쳐서 전송)"
        )
        self.transcript_correction_selector.setChecked(
            transcript_correction_enabled
        )
        self.transcript_correction_selector.setToolTip(
            "음성인식 결과에 이상한 단어가 많을 때, 채팅 모델이 소리가 비슷한 "
            "올바른 단어로 고쳐서 전송합니다. 끄면 인식 결과를 그대로 전송합니다."
        )
        form.addRow(self.transcript_correction_selector)
        self.nim_api_key_selector.setText(nim_api_key)
        self.engine_status = QLabel()
        self.engine_status.setWordWrap(True)
        self.engine_status.setStyleSheet(f"color: {MUTED};")
        form.addRow(self.engine_status)
        self.engine_selector.currentIndexChanged.connect(
            lambda _index: self._update_engine_ui()
        )
        self.nim_api_key_selector.textChanged.connect(
            lambda _text: self._update_engine_ui()
        )
        self.tool_rounds_selector = QSpinBox()
        self.tool_rounds_selector.setRange(0, agent.MAX_TOOL_ROUNDS)
        self.tool_rounds_selector.setValue(max_tool_rounds)
        self.tool_rounds_selector.setSpecialValueText("무제한")
        self.tool_rounds_selector.setSuffix(" 단계")
        self.tool_rounds_selector.setToolTip(
            "요청에서 도구를 호출하고 결과를 반영하는 최대 반복 횟수. "
            "0으로 두면 작업이 끝날 때까지 무제한 반복합니다 "
            "(중지 버튼으로 언제든 멈출 수 있습니다)."
        )
        form.addRow("도구 반복 한도", self.tool_rounds_selector)
        self.tool_calling_mode_selector = QComboBox()
        self.tool_calling_mode_selector.addItem("기존 방식 (모든 도구 제공)", "legacy")
        self.tool_calling_mode_selector.addItem("동적 방식 (카테고리별 로드)", "dynamic")
        mode_index = self.tool_calling_mode_selector.findData(tool_calling_mode)
        self.tool_calling_mode_selector.setCurrentIndex(max(0, mode_index))
        self.tool_calling_mode_selector.setToolTip(
            "동적 방식은 먼저 도구 카테고리를 선택하고 필요한 도구만 요청별로 제공합니다."
        )
        form.addRow("도구 호출 방식", self.tool_calling_mode_selector)
        self.desktop_control_enabled_selector = QCheckBox(
            "데스크톱 제어 도구 사용 (마우스·키보드 조작)"
        )
        self.desktop_control_enabled_selector.setChecked(desktop_control_enabled)
        self.desktop_control_enabled_selector.setToolTip(
            "켜면 데스크톱 화면 캡처와 마우스·키보드 조작 도구의 설명이 AI에게 "
            "전달됩니다. 꺼져 있으면 해당 도구는 AI에게 제공되지 않습니다."
        )
        form.addRow(self.desktop_control_enabled_selector)
        self.desktop_control_seconds_selector = QSpinBox()
        self.desktop_control_seconds_selector.setRange(
            MIN_SESSION_SECONDS,
            MAX_SESSION_SECONDS,
        )
        self.desktop_control_seconds_selector.setValue(desktop_control_seconds)
        self.desktop_control_seconds_selector.setSuffix("초")
        self.desktop_control_seconds_selector.setToolTip(
            "사용자가 직접 시작한 화면 제어 세션의 최대 시간 (10~300초)"
        )
        form.addRow("화면 제어 시간", self.desktop_control_seconds_selector)
        self.desktop_control_seconds_selector.setEnabled(desktop_control_enabled)
        self.desktop_control_enabled_selector.toggled.connect(
            self.desktop_control_seconds_selector.setEnabled
        )
        layout.addLayout(form)

        self.microphone_status = QLabel()
        self.microphone_status.setWordWrap(True)
        self.microphone_status.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(self.microphone_status)

        self.refresh_microphones_button = QPushButton("마이크 목록 새로고침")
        self.refresh_microphones_button.clicked.connect(
            lambda: self.refresh_microphones()
        )
        layout.addWidget(self.refresh_microphones_button)

        audio_test_description = QLabel(
            "녹음 후 재생하면 AI 인식기에 전달되는 처리된 소리를 확인할 수 있습니다."
        )
        audio_test_description.setWordWrap(True)
        audio_test_description.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(audio_test_description)

        audio_test_buttons = QHBoxLayout()
        self.audio_test_record_button = QPushButton("녹음 테스트 시작")
        self.audio_test_record_button.clicked.connect(
            self._toggle_audio_test_recording
        )
        audio_test_buttons.addWidget(self.audio_test_record_button)
        self.audio_test_play_button = QPushButton("처리된 음성 듣기")
        self.audio_test_play_button.setEnabled(False)
        self.audio_test_play_button.clicked.connect(self._toggle_audio_test_playback)
        audio_test_buttons.addWidget(self.audio_test_play_button)
        layout.addLayout(audio_test_buttons)

        self.audio_test_status = QLabel("선택한 마이크를 녹음하고 확인하세요.")
        self.audio_test_status.setWordWrap(True)
        self.audio_test_status.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(self.audio_test_status)

        self.audio_test_signals = AppSignals(self)
        self.audio_test_signals.audio_test_ready.connect(self._audio_test_ready)
        self.audio_test_signals.audio_test_failed.connect(self._audio_test_failed)
        self.audio_test_signals.audio_test_playback_finished.connect(
            self._audio_test_playback_finished
        )
        self.audio_test_signals.audio_test_playback_failed.connect(
            self._audio_test_playback_failed
        )
        self.audio_test_samples: Any = None
        self.audio_test_stop_event: threading.Event | None = None
        self.audio_test_worker: VoiceInputTestWorker | None = None
        self.audio_playback_stop_event: threading.Event | None = None
        self.audio_playback_worker: VoicePlaybackTestWorker | None = None

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_microphones(selected_device_index)
        self._update_engine_ui()

    @property
    def selected_device_index(self) -> int | None:
        device_index = self.microphone_selector.currentData()
        if isinstance(device_index, int) and device_index >= 0:
            return device_index
        return None

    @property
    def selected_device_name(self) -> str:
        return self.microphone_selector.currentText()

    @property
    def max_tool_rounds(self) -> int:
        return self.tool_rounds_selector.value()

    @property
    def tool_calling_mode(self) -> str:
        return str(self.tool_calling_mode_selector.currentData())

    @property
    def desktop_control_seconds(self) -> int:
        return self.desktop_control_seconds_selector.value()

    @property
    def desktop_control_enabled(self) -> bool:
        return self.desktop_control_enabled_selector.isChecked()

    @property
    def recognition_engine(self) -> str:
        return str(self.engine_selector.currentData())

    @property
    def whisper_model(self) -> str:
        return str(self.whisper_model_selector.currentData())

    @property
    def transcript_correction_enabled(self) -> bool:
        return self.transcript_correction_selector.isChecked()

    @property
    def nvidia_api_key(self) -> str:
        return self.nim_api_key_selector.text().strip()

    def _update_engine_ui(self) -> None:
        engine = self.recognition_engine
        self.whisper_model_selector.setEnabled(engine == VOICE_ENGINE_WHISPER)
        self.nim_api_key_selector.setEnabled(engine == VOICE_ENGINE_NIM)
        if engine == VOICE_ENGINE_WHISPER:
            if _faster_whisper_available():
                self.engine_status.setText(
                    "Whisper 엔진을 사용할 준비가 되었습니다."
                )
            else:
                self.engine_status.setText(
                    "faster-whisper가 설치되지 않았습니다. "
                    "pip install faster-whisper 실행 후 사용하세요."
                )
            return
        if engine == VOICE_ENGINE_NIM:
            if not _riva_client_available():
                self.engine_status.setText(
                    "nvidia-riva-client가 설치되지 않았습니다. "
                    "pip install nvidia-riva-client 실행 후 사용하세요."
                )
                return
            if self.nvidia_api_key:
                self.engine_status.setText(
                    "whisper-large-v3 클라우드 인식을 사용할 준비가 되었습니다."
                    " (무료 체험: 분당 약 40회 요청)"
                )
            else:
                self.engine_status.setText(
                    "NVIDIA API 키를 입력해 주세요. build.nvidia.com에서 무료로 "
                    "발급할 수 있습니다."
                )
            return
        if engine == VOICE_ENGINE_OMNI:
            self.engine_status.setText(
                "녹음한 음성을 omni 모델에 직접 첨부해 모델이 스스로 듣고 답합니다. "
                "공급자를 omni로 선택하면 NVIDIA NIM의 "
                "nemotron-3-nano-omni 모델(NVIDIA_API_KEY 재사용)이 "
                "음성을 직접 인식합니다."
            )
            return
        self.engine_status.setText("")

    def _toggle_audio_test_recording(self) -> None:
        if self.audio_test_worker is not None and self.audio_test_worker.is_alive():
            if self.audio_test_stop_event is not None:
                self.audio_test_stop_event.set()
            self.audio_test_record_button.setEnabled(False)
            self.audio_test_status.setText("녹음을 마무리하는 중입니다...")
            return

        selected_device_index = self.selected_device_index
        self.audio_test_stop_event = threading.Event()
        self.audio_test_worker = VoiceInputTestWorker(
            self.audio_test_stop_event,
            self.audio_test_signals,
            selected_device_index,
        )
        self.microphone_selector.setEnabled(False)
        self.refresh_microphones_button.setEnabled(False)
        self.audio_test_record_button.setText("녹음 중지")
        self.audio_test_play_button.setEnabled(False)
        self.audio_test_status.setText(
            "녹음 중입니다. 말한 뒤 '녹음 중지'를 누르세요."
        )
        self.audio_test_worker.start()

    def _audio_test_ready(self, samples: Any) -> None:
        self.audio_test_samples = samples
        self._reset_audio_test_recording_controls()
        self.audio_test_play_button.setEnabled(True)
        self.audio_test_status.setText(
            "녹음 완료. '처리된 음성 듣기'로 AI가 받는 소리를 확인하세요."
        )

    def _audio_test_failed(self, error: str) -> None:
        self._reset_audio_test_recording_controls()
        self.audio_test_status.setText(f"마이크 테스트 실패: {error}")

    def _reset_audio_test_recording_controls(self) -> None:
        self.audio_test_worker = None
        self.audio_test_stop_event = None
        self.microphone_selector.setEnabled(True)
        self.refresh_microphones_button.setEnabled(True)
        self.audio_test_record_button.setEnabled(True)
        self.audio_test_record_button.setText("녹음 테스트 시작")

    def _toggle_audio_test_playback(self) -> None:
        if (
            self.audio_playback_worker is not None
            and self.audio_playback_worker.is_alive()
        ):
            if self.audio_playback_stop_event is not None:
                self.audio_playback_stop_event.set()
            self.audio_test_play_button.setEnabled(False)
            self.audio_test_status.setText("재생을 중지하는 중입니다...")
            return
        if self.audio_test_samples is None:
            return

        self.audio_playback_stop_event = threading.Event()
        self.audio_playback_worker = VoicePlaybackTestWorker(
            self.audio_test_samples,
            self.audio_playback_stop_event,
            self.audio_test_signals,
        )
        self.audio_test_play_button.setText("재생 중지")
        self.audio_test_status.setText(
            "AI 인식기에 전달되는 처리 음성을 재생합니다..."
        )
        self.audio_playback_worker.start()

    def _audio_test_playback_finished(self) -> None:
        self._reset_audio_test_playback_controls()
        self.audio_test_status.setText("재생이 끝났습니다.")

    def _audio_test_playback_failed(self, error: str) -> None:
        self._reset_audio_test_playback_controls()
        self.audio_test_status.setText(f"음성 재생 실패: {error}")

    def _reset_audio_test_playback_controls(self) -> None:
        self.audio_playback_worker = None
        self.audio_playback_stop_event = None
        self.audio_test_play_button.setEnabled(self.audio_test_samples is not None)
        self.audio_test_play_button.setText("처리된 음성 듣기")

    def _stop_audio_test_workers(self) -> None:
        if self.audio_test_stop_event is not None:
            self.audio_test_stop_event.set()
        if self.audio_playback_stop_event is not None:
            self.audio_playback_stop_event.set()

    def accept(self) -> None:
        self._stop_audio_test_workers()
        super().accept()

    def reject(self) -> None:
        self._stop_audio_test_workers()
        super().reject()

    def refresh_microphones(self, preferred_device_index: int | None = None) -> None:
        if preferred_device_index is None:
            current_index = self.microphone_selector.currentData()
            if isinstance(current_index, int) and current_index >= 0:
                preferred_device_index = current_index

        self.microphone_selector.clear()
        self.microphone_selector.addItem("시스템 기본 마이크", -1)

        try:
            import sounddevice as sd
        except ImportError as error:
            self.microphone_selector.setEnabled(False)
            self.microphone_status.setText(
                f"마이크 기능을 사용할 수 없습니다: {error}"
            )
            return

        try:
            devices = sd.query_devices()
        except sd.PortAudioError as error:
            self.microphone_selector.setEnabled(False)
            self.microphone_status.setText(
                f"마이크 목록을 불러오지 못했습니다: {error}"
            )
            return

        self.microphone_selector.setEnabled(True)
        available_devices: list[int] = []
        for device_index, device in enumerate(devices):
            if int(device["max_input_channels"]) <= 0:
                continue
            self.microphone_selector.addItem(
                f"{device['name']} (입력 {device_index})",
                device_index,
            )
            available_devices.append(device_index)

        if preferred_device_index in available_devices:
            selected_index = self.microphone_selector.findData(preferred_device_index)
            self.microphone_selector.setCurrentIndex(selected_index)
            self.microphone_status.setText("사용할 마이크를 선택하세요.")
        elif preferred_device_index is not None:
            self.microphone_status.setText(
                "저장된 마이크를 찾을 수 없어 시스템 기본 마이크를 선택했습니다."
            )
        elif available_devices:
            self.microphone_status.setText("사용할 마이크를 선택하세요.")
        else:
            self.microphone_status.setText(
                "사용 가능한 입력 마이크가 없습니다. 연결 후 목록을 새로고침하세요."
            )

    def apply_engine_state(self) -> None:
        self._update_engine_ui()


def find_input_device_index(
    devices: Any,
    device_name: str,
    host_api: int,
    input_channels: int | None = None,
) -> int | None:
    fallback_index: int | None = None
    for device_index, device in enumerate(devices):
        if (
            device["name"] == device_name
            and int(device["hostapi"]) == host_api
            and int(device["max_input_channels"]) > 0
        ):
            if (
                input_channels is not None
                and int(device["max_input_channels"]) == input_channels
            ):
                return device_index
            if fallback_index is None:
                fallback_index = device_index
    return fallback_index


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def _riva_client_available() -> bool:
    try:
        import riva.client  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


class LocalSpeechRecognizer:
    def __init__(self) -> None:
        self.model_lock = threading.Lock()
        self.runtime_environment: dict[str, str] | None = None
        self.runtime_temp_dir: Path | None = None
        self.server_process: subprocess.Popen[bytes] | None = None
        self.server_url: str | None = None
        self.server_model: str | None = None
        self.server_api_key: str | None = None
        self.server_client: Any = None
        self.server_log_path: Path | None = None
        self.server_temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.server_lock = threading.Lock()
        self.closed = False

    def _prepare_runtime_paths(self) -> tuple[dict[str, str], Path]:
        environment = os.environ.copy()
        temp_dir = Path(tempfile.gettempdir())
        local_app_data = environment.get("LOCALAPPDATA")
        model_dir = environment.get("NEMO_SPEECH_MODEL_DIR")
        if not model_dir and local_app_data:
            model_dir = str(Path(local_app_data) / "NeMoSpeech" / "models")

        if os.name == "nt" and (
            not str(temp_dir).isascii()
            or (model_dir is not None and not model_dir.isascii())
        ):
            identity = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            identity_rows = list(csv.reader(identity.stdout.splitlines()))
            if not identity_rows or len(identity_rows[0]) < 2:
                raise RuntimeError("현재 Windows 사용자 SID를 확인할 수 없습니다.")
            user_sid = identity_rows[0][1]
            public_dir = Path(environment.get("PUBLIC", r"C:\Users\Public"))
            private_dir = public_dir / "QuasarNeMo" / user_sid
            private_dir.mkdir(parents=True, exist_ok=True)
            system_root = environment.get("SystemRoot", r"C:\Windows")
            icacls = str(Path(system_root) / "System32" / "icacls.exe")
            acl_result = subprocess.run(
                [
                    icacls,
                    str(private_dir),
                    "/inheritance:r",
                    "/grant:r",
                    f"*{user_sid}:(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F",
                    "*S-1-5-32-544:(OI)(CI)F",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if acl_result.returncode != 0:
                raise RuntimeError(
                    "양자화 모델용 비공개 Windows 캐시 권한 설정에 실패했습니다: "
                    f"{acl_result.stderr.strip() or acl_result.stdout.strip()}"
                )

            temp_dir = private_dir / "temp"
            temp_dir.mkdir(exist_ok=True)
            if model_dir is None or not model_dir.isascii():
                environment["NEMO_SPEECH_MODEL_DIR"] = str(
                    private_dir / "models"
                )

        return environment, temp_dir

    def _find_executable(self) -> str:
        executable = shutil.which("nemo-speech")
        if executable is None:
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                candidate = (
                    Path(local_app_data)
                    / "Programs"
                    / "NeMoSpeech"
                    / "bin"
                    / "nemo-speech.exe"
                )
                if candidate.is_file():
                    executable = str(candidate)
        if executable is None:
            raise RuntimeError(
                "양자화 음성 인식 런타임이 없습니다. NVIDIA NeMo-Speech.cpp를 "
                "설치한 뒤 새 터미널에서 다시 실행해 주세요."
            )
        return executable

    def start_server(self) -> None:
        with self.server_lock:
            if self.closed:
                raise RuntimeError("음성 인식기가 종료되었습니다.")
            if self.server_process is not None:
                if self.server_process.poll() is None:
                    return
                self._release_server_resources()

            if self.runtime_environment is None or self.runtime_temp_dir is None:
                (
                    self.runtime_environment,
                    self.runtime_temp_dir,
                ) = self._prepare_runtime_paths()

            executable = self._find_executable()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]

            self.server_temp_dir = tempfile.TemporaryDirectory(
                prefix="quasar-asr-server-",
                dir=self.runtime_temp_dir,
            )
            self.server_log_path = Path(self.server_temp_dir.name) / "server.log"
            self.server_api_key = secrets.token_urlsafe(32)
            command = [
                executable,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--asr-model",
                NEMO_SPEECH_MODEL,
                "--device",
                "auto",
                "--no-ui",
                "--api-key",
                self.server_api_key,
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                with self.server_log_path.open("wb") as log_file:
                    self.server_process = subprocess.Popen(
                        command,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        env=self.runtime_environment,
                        creationflags=creation_flags,
                    )
            except OSError as error:
                self._release_server_resources()
                raise RuntimeError(
                    f"로컬 음성 인식 서버를 시작하지 못했습니다: {error}"
                ) from error
            self.server_url = f"http://127.0.0.1:{port}"

    def _release_server_resources(self) -> None:
        self.server_process = None
        self.server_url = None
        self.server_model = None
        self.server_api_key = None
        self.server_log_path = None
        if self.server_temp_dir is not None:
            self.server_temp_dir.cleanup()
            self.server_temp_dir = None
        if self.server_client is not None:
            self.server_client.close()
            self.server_client = None

    def _server_error_detail(self) -> str:
        if self.server_log_path is None or not self.server_log_path.is_file():
            return ""
        return self.server_log_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()

    def _wait_for_server(self, stop_event: threading.Event | None = None) -> None:
        self.start_server()
        server_url = self.server_url
        server_process = self.server_process
        if server_url is None or server_process is None:
            raise RuntimeError("로컬 음성 인식 서버 주소를 설정하지 못했습니다.")
        if self.server_model is not None:
            return

        deadline = time.monotonic() + 900
        ready_url = f"{server_url}/ready"
        models_url = f"{server_url}/v1/models"
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("음성 인식 요청이 취소되었습니다.")
            process_status = server_process.poll()
            if process_status is not None:
                detail = self._server_error_detail()
                raise RuntimeError(
                    "로컬 음성 인식 서버가 준비되기 전에 종료되었습니다"
                    f" (코드 {process_status}). {detail}"
                )
            try:
                with urlopen(ready_url, timeout=1) as response:
                    readiness = json.loads(response.read())
                if readiness.get("ready") is True:
                    if self.server_api_key is None:
                        raise RuntimeError(
                            "로컬 음성 인식 서버 API 키가 설정되지 않았습니다."
                        )
                    request = Request(
                        models_url,
                        headers={
                            "Authorization": f"Bearer {self.server_api_key}"
                        },
                    )
                    with urlopen(request, timeout=1) as response:
                        models = json.loads(response.read()).get("data", [])
                    asr_model = next(
                        (
                            model
                            for model in models
                            if model.get("capability") == "transcription"
                        ),
                        None,
                    )
                    if asr_model is None and models:
                        asr_model = models[0]
                    if asr_model is None or not isinstance(asr_model.get("id"), str):
                        raise RuntimeError(
                            "로컬 음성 인식 서버가 사용할 모델을 제공하지 않습니다."
                        )
                    self.server_model = asr_model["id"]
                    return
                time.sleep(0.15)
            except (URLError, TimeoutError, OSError):
                time.sleep(0.15)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"로컬 음성 인식 서버 응답을 읽을 수 없습니다: {error}"
                ) from error

        raise TimeoutError(
            "로컬 음성 인식 서버가 15분 안에 준비되지 않았습니다. "
            f"{self._server_error_detail()}"
        )

    def _get_server_client(self) -> Any:
        with self.server_lock:
            if self.closed:
                raise RuntimeError("음성 인식기가 종료되었습니다.")
            if self.server_client is None:
                from openai import OpenAI

                if (
                    self.server_url is None
                    or self.server_model is None
                    or self.server_api_key is None
                ):
                    raise RuntimeError("로컬 음성 인식 서버가 시작되지 않았습니다.")
                self.server_client = OpenAI(
                    base_url=f"{self.server_url}/v1",
                    api_key=self.server_api_key,
                    timeout=900,
                    max_retries=0,
                )
            return self.server_client

    def close(self) -> None:
        with self.server_lock:
            self.closed = True
            process = self.server_process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            self._release_server_resources()

    def transcribe(
        self,
        audio: Any,
        signals: AppSignals,
        *,
        conversation: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        with self.model_lock:
            import numpy as np

            signals.voice_status.emit("로컬 음성 인식기를 준비하는 중입니다...")
            self._wait_for_server(stop_event)
            signals.voice_status.emit("음성을 로컬에서 인식하는 중입니다...")
            samples = np.asarray(audio, dtype=np.float32)
            pcm_samples = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")

            with tempfile.TemporaryDirectory(
                prefix="quasar-asr-",
                dir=self.runtime_temp_dir,
            ) as temp_dir:
                audio_path = Path(temp_dir) / "recording.wav"
                with wave.open(str(audio_path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(VOICE_RECOGNITION_SAMPLE_RATE)
                    wav_file.writeframes(pcm_samples.tobytes())

                client = self._get_server_client()
                with audio_path.open("rb") as audio_file:
                    try:
                        result = client.audio.transcriptions.create(
                            model=self.server_model,
                            file=audio_file,
                            language=VOICE_LANGUAGE,
                        )
                    except Exception:
                        # 서버가 language 파라미터를 지원하지 않으면 최초
                        # 파라미터 없이 다시 시도한다.
                        audio_file.seek(0)
                        result = client.audio.transcriptions.create(
                            model=self.server_model,
                            file=audio_file,
                        )

            transcript = result.text.strip()
            transcript = re.sub(r"\s+[a-z]{2,3}-[A-Z]{2,3}\s*$", "", transcript)

        if not transcript:
            raise RuntimeError("음성을 인식하지 못했습니다. 다시 말씀해 주세요.")
        if conversation:
            signals.conversation_voice_text_ready.emit(transcript)
        else:
            signals.voice_text_ready.emit(transcript)


class WhisperSpeechRecognizer:
    """faster-whisper 기반 로컬 음성 인식기.

    NeMo 인식기(LocalSpeechRecognizer)와 동일한 인터페이스를 제공하므로
    음성 워커들은 인식기 종류를 구분하지 않고 사용할 수 있습니다.
    """

    def __init__(self, model_name: str = WHISPER_DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self.model_lock = threading.Lock()
        self.model: Any = None
        self.loaded_model_name: str | None = None
        self.closed = False

    def start_server(self) -> None:
        # Whisper는 별도 서버가 필요 없다. 첫 인식 전에 모델을 미리 불러 둔다.
        with self.model_lock:
            if self.closed:
                return
            if self._has_ready_model():
                return
            try:
                self._load_model()
            except (ImportError, OSError, RuntimeError, ValueError):
                # 미리 불러오기에 실패하면 transcribe에서 다시 시도한다.
                pass

    def _has_ready_model(self) -> bool:
        return (
            self.model is not None
            and self.loaded_model_name == self.model_name
        )

    def _pick_model_device(self) -> tuple[str, str]:
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "int8_float16"
        except Exception:
            # ctranslate2 임포트 실패 등은 CPU로 진행한다.
            pass
        return "cpu", "int8"

    def _load_model(self) -> Any:
        from faster_whisper import WhisperModel

        # 이전 모델이 남아 있으면 먼저 해제한다. 두 벌이 동시에 잡히면
        # medium 이상 모델에서 mkl_malloc 메모리 부족이 난다.
        self.model = None
        self.loaded_model_name = None
        gc.collect()

        device, compute_type = self._pick_model_device()
        try:
            model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute_type,
            )
        except (OSError, RuntimeError, MemoryError) as error:
            detail = f"{type(error).__name__}: {error}"
            if "memory" in str(error).lower() or isinstance(
                error,
                MemoryError,
            ):
                raise RuntimeError(
                    "Whisper 모델을 메모리에 불러오지 못했습니다. 다른 프로그램의 "
                    "메모리를 줄이거나, 설정에서 더 작은 Whisper 모델을 선택해 "
                    "주세요. 상세 정보: " + detail
                ) from error
            raise
        self.model = model
        self.loaded_model_name = self.model_name
        return model

    def _get_ready_model(self) -> Any:
        # 이미 불러 둔 같은 이름의 모델은 매 음성 입력마다 다시 만들지 않는다.
        if self.closed:
            raise RuntimeError("음성 인식기가 종료되었습니다.")
        if self._has_ready_model():
            return self.model
        try:
            return self._load_model()
        except (OSError, RuntimeError, MemoryError) as error:
            message = str(error)
            if not isinstance(error, RuntimeError) or "Whisper 모델" not in message:
                raise RuntimeError(
                    f"Whisper 모델을 준비하지 못했습니다. {type(error).__name__}: {error}"
                ) from error
            raise

    def close(self) -> None:
        with self.model_lock:
            self.closed = True
            self.model = None
            self.loaded_model_name = None

    def transcribe(
        self,
        audio: Any,
        signals: AppSignals,
        *,
        conversation: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        import numpy as np

        with self.model_lock:
            if self.closed:
                raise RuntimeError("음성 인식기가 종료되었습니다.")
            try:
                signals.voice_status.emit(
                    "Whisper 모델을 준비하는 중입니다..."
                )
                model = self._get_ready_model()
            except ImportError as error:
                raise RuntimeError(
                    "faster-whisper가 설치되지 않았습니다. "
                    "pip install faster-whisper 를 실행한 뒤 다시 시도해 주세요."
                ) from error

            signals.voice_status.emit("음성을 Whisper로 인식하는 중입니다...")
            samples = np.asarray(audio, dtype=np.float32)
            segments, _info = model.transcribe(
                samples,
                language=VOICE_LANGUAGE,
                beam_size=5,
                condition_on_previous_text=False,
                without_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=250,
                    speech_pad_ms=40,
                ),
                initial_prompt=VOICE_INITIAL_PROMPT,
            )

            texts: list[str] = []
            for segment in segments:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("음성 인식 요청이 취소되었습니다.")
                segment_text = str(segment.text).strip()
                if segment_text:
                    texts.append(segment_text)
            transcript = " ".join(texts)
            if transcript.startswith(VOICE_INITIAL_PROMPT):
                transcript = transcript[len(VOICE_INITIAL_PROMPT) :].strip()

        if not transcript:
            raise RuntimeError(
                "음성을 인식하지 못했습니다. 다시 말씀해 주세요."
            )
        if conversation:
            signals.conversation_voice_text_ready.emit(transcript)
        else:
            signals.voice_text_ready.emit(transcript)


def normalize_voice_engine(value: Any) -> str:
    return value if value in VOICE_ENGINE_LABELS else VOICE_ENGINE_NEMO


def create_voice_recognizer(
    engine: str,
    whisper_model: str = WHISPER_DEFAULT_MODEL,
    nim_api_key: str | None = None,
) -> (
    LocalSpeechRecognizer
    | WhisperSpeechRecognizer
    | NimSpeechRecognizer
    | OmniSpeechRecognizer
):
    engine = normalize_voice_engine(engine)
    if engine == VOICE_ENGINE_WHISPER:
        return WhisperSpeechRecognizer(whisper_model)
    if engine == VOICE_ENGINE_NIM:
        return NimSpeechRecognizer(nim_api_key)
    if engine == VOICE_ENGINE_OMNI:
        return OmniSpeechRecognizer()
    return LocalSpeechRecognizer()


def encode_voice_wav(samples: Any) -> bytes:
    """16kHz 모노 16비트 WAV로 녹음 샘플을 인코딩한다."""
    import numpy as np

    mono = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (mono * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(VOICE_RECOGNITION_SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


class OmniSpeechRecognizer:
    """omni 모델(옴니모달)이 음성을 직접 듣게 하는 인식기.

    로컬 STT를 건너뛰고 녹음을 16kHz WAV로 인코딩해 base64로 담은 뒤,
    요청할 때 OpenAI 호환 `input_audio` 콘텐츠로 채팅 모델에 직접 전달한다.
    omni 모델(qwen3-omni 등)은 이 음성을 스스로 이해해 답변한다.
    """

    def __init__(self) -> None:
        self.model_lock = threading.Lock()
        self.closed = False

    def start_server(self) -> None:
        # 음성은 채팅 요청에 함께 첨부되므로 미리 준비할 서버는 없다.
        return None

    def close(self) -> None:
        self.closed = True

    def transcribe(
        self,
        audio: Any,
        signals: AppSignals,
        *,
        conversation: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        import numpy as np

        with self.model_lock:
            if self.closed:
                raise RuntimeError("음성 인식기가 종료되었습니다.")
            samples = np.asarray(audio, dtype=np.float32)
            if samples.size * 2 < VOICE_RECOGNITION_SAMPLE_RATE:
                raise RuntimeError(
                    "녹음이 너무 짧습니다. 조금 더 길게 말씀해 주세요."
                )
            signals.voice_status.emit(
                "음성을 omni 모델에 직접 전달하는 중입니다..."
            )
            wav_bytes = encode_voice_wav(samples)
            audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("음성 인식 요청이 취소되었습니다.")
        signals.voice_audio_ready.emit(audio_b64)
        if conversation:
            signals.conversation_voice_text_ready.emit(VOICE_OMNI_PLACEHOLDER)
        else:
            signals.voice_text_ready.emit(VOICE_OMNI_PLACEHOLDER)


def _voice_style_messages(
    app: Any,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """음성으로 읽어 줄 요청에는 음성 대화 규칙을 시스템 프롬프트에 덧붙인다."""
    response_toggle = getattr(app, "voice_response_toggle", None)
    will_be_spoken = bool(
        getattr(app, "voice_conversation_active", False)
        or (
            response_toggle is not None
            and response_toggle.isChecked()
        )
    )
    if not will_be_spoken:
        return messages
    result = list(messages)
    if result and result[0].get("role") == "system":
        first = dict(result[0])
        content = str(first.get("content", ""))
        if "음성 대화 모드 규칙" not in content:
            first["content"] = content.rstrip() + "\n\n" + VOICE_RESPONSE_SYSTEM_PROMPT
        result[0] = first
    else:
        result.insert(
            0,
            {"role": "system", "content": VOICE_RESPONSE_SYSTEM_PROMPT},
        )
    return result


def _strip_voice_style_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """대화 기록을 저장하기 전에 음성 대화 규칙 문구를 떼어낸다.

    음성 스타일 규칙은 요청할 때마다 _voice_style_messages가 새로 덧붙이는
    표시용 문구이므로, 기록 자체에는 남기지 않는다.
    """
    result = list(messages)
    if result and result[0].get("role") == "system":
        first = dict(result[0])
        content = str(first.get("content", ""))
        suffix = "\n\n" + VOICE_RESPONSE_SYSTEM_PROMPT
        if content.endswith(suffix):
            first["content"] = content[: -len(suffix)].rstrip()
            result[0] = first
    return result


def _session_for_tool_context(owner: Any) -> Any:
    """요청을 실행 중인 스레드의 세션(작업 창)을 돌려준다. 없으면 owner.

    병렬 작업 창은 자기 스레드에서 ``owner._tool_context.session``을 설정해
    도구 진행 메시지와 승인 요청이 그 창으로 전달되게 한다. 컨텍스트가 없는
    테스트용 목 객체에서는 owner를 그대로 사용한다.
    """
    context = getattr(owner, "_tool_context", None)
    session = getattr(context, "session", None) if context is not None else None
    return session if session is not None else owner


class NimSpeechRecognizer:
    """build.nvidia.com에 호스팅된 whisper-large-v3(NVIDIA NIM) 인식기.

    무료 체험 엔드포인트를 gRPC(Riva 클라이언트)로 호출한다. 모델 자체는
    클라우드에서 돌아가므로 이 PC의 RAM/CPU는 거의 쓰지 않는다.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key or os.getenv(VOICE_NIM_API_KEY_ENV, "") or "").strip()
        self.model_lock = threading.Lock()
        self.closed = False

    def start_server(self) -> None:
        # 외부 호스팅 서버라 미리 준비할 서버는 없고 키 존재만 확인한다.
        if not self.api_key:
            raise RuntimeError(
                "NVIDIA API 키가 설정되지 않았습니다. 설정에서 유효한 API 키를 "
                f"입력하거나 환경 변수 {VOICE_NIM_API_KEY_ENV}에 지정해 주세요."
            )

    def close(self) -> None:
        self.closed = True

    def transcribe(
        self,
        audio: Any,
        signals: AppSignals,
        *,
        conversation: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        import numpy as np

        with self.model_lock:
            if self.closed:
                raise RuntimeError("음성 인식기가 종료되었습니다.")
            try:
                import riva.client
            except ImportError as error:
                raise RuntimeError(
                    "nvidia-riva-client가 설치되지 않았습니다. "
                    "pip install nvidia-riva-client 를 실행한 뒤 다시 시도해 주세요."
                ) from error
            if not self.api_key:
                raise RuntimeError(
                    "NVIDIA API 키가 설정되지 않았습니다. 설정에서 유효한 API 키를 "
                    f"입력하거나 환경 변수 {VOICE_NIM_API_KEY_ENV}에 지정해 주세요."
                )

            signals.voice_status.emit(
                "음성을 NVIDIA 클라우드(whisper-large-v3)로 인식하는 중입니다..."
            )
            samples = np.asarray(audio, dtype=np.float32)
            pcm_samples = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")

            # hosted whisper NIM의 오디오 디코더는 헤더 없는 raw PCM을
            # 음성으로 인식하지 못한다. 반드시 완전한 WAV 파일을 보낸다.
            auth = riva.client.Auth(
                use_ssl=True,
                uri=VOICE_NIM_SERVER,
                metadata_args=[
                    ["function-id", VOICE_NIM_FUNCTION_ID],
                    ["authorization", f"Bearer {self.api_key}"],
                ],
            )
            service = riva.client.ASRService(auth)
            config = riva.client.RecognitionConfig(
                language_code=VOICE_LANGUAGE,
                max_alternatives=1,
                enable_automatic_punctuation=True,
                verbatim_transcripts=True,
            )
            with tempfile.TemporaryDirectory(
                prefix="quasar-nim-",
            ) as temp_dir:
                audio_path = Path(temp_dir) / "recording.wav"
                with wave.open(str(audio_path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(VOICE_RECOGNITION_SAMPLE_RATE)
                    wav_file.writeframes(pcm_samples.tobytes())
                audio_bytes = audio_path.read_bytes()
            try:
                response = service.offline_recognize(audio_bytes, config)
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                if "401" in detail or "403" in detail or "AuthContext" in detail:
                    raise RuntimeError(
                        "NVIDIA API 키가 거부되었습니다. build.nvidia.com에서 새 "
                        f"API 키를 발급해 주세요. 상세 정보: {detail}"
                    ) from error
                if "RATE_LIMIT" in detail.upper() or "429" in detail:
                    raise RuntimeError(
                        "NVIDIA 무료 체험 요청 한도(분당 요청 수)를 초과했습니다. "
                        f"잠시 후 다시 시도해 주세요. 상세 정보: {detail}"
                    ) from error
                raise RuntimeError(f"NVIDIA 클라우드 인식 요청 실패: {detail}") from error

            texts: list[str] = []
            for result in response.results:
                for alternative in result.alternatives:
                    segment_text = str(alternative.transcript).strip()
                    if segment_text:
                        texts.append(segment_text)
            transcript = " ".join(texts)
            transcript = re.sub(r"\s+[a-z]{2,3}-[A-Z]{2,3}\s*$", "", transcript)

        if not transcript:
            raise RuntimeError("음성을 인식하지 못했습니다. 다시 말씀해 주세요.")
        if conversation:
            signals.conversation_voice_text_ready.emit(transcript)
        else:
            signals.voice_text_ready.emit(transcript)


class TranscriptCorrectionWorker(threading.Thread):
    """음성인식 문장을 채팅 모델로 한 번 더 교정하는 워커.

    음성인식이 '크록 브라우드 켜봐'처럼 비슷한 소리의 단어로 잘못 기록하는 것을
    문맥을 아는 채팅 모델이 '크롬 브라우저 켜봐'로 되살려 준다. 교정에는
    실패하거나 시간이 초과되면 원문을 그대로 전달한다.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        text: str,
        signals: AppSignals,
        *,
        conversation: bool,
    ) -> None:
        super().__init__(daemon=True)
        self.client = client
        self.model = model
        self.text = text
        self.signals = signals
        self.conversation = conversation

    def _request(self) -> str | None:
        messages = [
            {"role": "system", "content": TRANSCRIPT_CORRECTION_SYSTEM_PROMPT},
            {"role": "user", "content": self.text},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                timeout=TRANSCRIPT_CORRECTION_TIMEOUT_SECONDS,
            )
        except TypeError:
            # timeout 파라미터를 지원하지 않는 클라이언트는 없이 시도한다.
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
            )
        choices = list(getattr(response, "choices", []) or [])
        content = ""
        if choices:
            content = str(choices[0].message.content or "")
        stripped = content.strip().strip("\"'`").strip()
        return stripped or None

    def run(self) -> None:
        corrected = self.text
        try:
            candidate = self._request()
        except Exception:
            candidate = None
        if candidate:
            corrected = candidate
        if self.conversation:
            self.signals.conversation_voice_text_final.emit(corrected)
        else:
            self.signals.voice_text_final.emit(corrected)


def _input_device_entries(sd: Any) -> list[tuple[int, str, int]]:
    """사용 가능한 입력 장치 목록을 조회한다. 실패하면 빈 목록을 돌려준다."""
    try:
        entries: list[tuple[int, str, int]] = []
        for device_index, device in enumerate(sd.query_devices()):
            if int(device["max_input_channels"]) < 1:
                continue
            sample_rate = int(device["default_samplerate"])
            if sample_rate <= 0:
                continue
            entries.append((device_index, str(device["name"]), sample_rate))
        return entries
    except Exception:
        # 전체 장치 목록 조회가 불가능한 환경(테스트용 가짜 모듈 등)도 있다.
        return []


@contextlib.contextmanager
def open_voice_capture_stream(
    sd: Any,
    device_index: int | None,
    callback: Any,
) -> Iterator[tuple[int, str | None]]:
    """음성 입력 RawInputStream을 열어 (샘플링 속도, 대체 안내)를 돌려준다.

    설정된 장치·기본 샘플링 속도 조합으로 스트림 열기가 실패하면
    (PaErrorCode -9996 Invalid device 등) 흔히 지원되는 샘플링 속도,
    시스템 기본 마이크, 나머지 입력 장치 순으로 다시 시도한다.
    """
    candidates: list[tuple[int | None, int, str | None]] = []
    seen: set[tuple[int | None, int]] = set()

    def add(candidate_device: int | None, sample_rate: int, note: str | None) -> None:
        if sample_rate <= 0:
            return
        key = (candidate_device, sample_rate)
        if key not in seen:
            seen.add(key)
            candidates.append((candidate_device, sample_rate, note))

    def default_input_rate() -> int:
        try:
            default_device = sd.query_devices(kind="input")
            if int(default_device["max_input_channels"]) < 1:
                return 0
            return int(default_device["default_samplerate"])
        except Exception:
            return 0

    fallback_note = "설정한 마이크를 열 수 없어 시스템 기본 마이크로 대체했습니다."
    if device_index is None:
        add(None, default_input_rate(), None)
        for sample_rate in VOICE_FALLBACK_SAMPLE_RATES:
            add(None, sample_rate, None)
    else:
        # 후보 목록 순서와 무관하게 장치 조회는 기본 장치를 먼저 한다.
        default_rate = default_input_rate()
        try:
            device = sd.query_devices(device_index)
        except Exception:
            device = None
        if device is not None and int(device["max_input_channels"]) < 1:
            raise RuntimeError(
                "선택한 장치는 마이크 입력을 지원하지 않습니다. "
                "설정에서 입력 마이크를 다시 선택해 주세요."
            )
        if device is not None:
            add(device_index, int(device["default_samplerate"]), None)
            for sample_rate in VOICE_FALLBACK_SAMPLE_RATES:
                add(device_index, sample_rate, None)
        add(None, default_rate, fallback_note)
        for sample_rate in VOICE_FALLBACK_SAMPLE_RATES:
            add(None, sample_rate, fallback_note)

    last_error: Exception | None = None
    enumerated = False
    position = 0
    while True:
        if position >= len(candidates) and not enumerated:
            enumerated = True
            configured = {
                candidate
                for candidate, _sample_rate, _note in candidates
                if candidate is not None
            }
            for other_index, name, rate in _input_device_entries(sd):
                if other_index not in configured:
                    add(
                        other_index,
                        rate,
                        f"마이크를 열 수 없어 다른 입력 장치 {name}(입력 {other_index})로"
                        " 대체했습니다.",
                    )
        if position >= len(candidates):
            break
        candidate_device, sample_rate, note = candidates[position]
        position += 1
        stream_opened = False
        try:
            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=callback,
                device=candidate_device,
            ) as stream:
                stream_opened = True
                yield sample_rate, note
                return
        except Exception as error:
            if stream_opened:
                raise
            last_error = error

    if last_error is None:
        raise RuntimeError(
            "사용할 수 있는 입력 마이크가 없습니다. 마이크 연결을 확인해 주세요."
        )
    raise RuntimeError(
        "마이크를 열 수 없습니다. 설정에서 다른 입력 장치를 선택해 주세요."
    ) from last_error


class VoiceCaptureWorker(threading.Thread):
    def __init__(
        self,
        stop_event: threading.Event,
        recognizer: LocalSpeechRecognizer | WhisperSpeechRecognizer,
        signals: AppSignals,
        device_index: int | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.recognizer = recognizer
        self.signals = signals
        self.device_index = device_index

    def run(self) -> None:
        try:
            import sounddevice as sd

            frames: list[bytes] = []
            callback_errors: list[str] = []

            def capture_audio(
                data: Any,
                _frame_count: int,
                _time_info: Any,
                status: Any,
            ) -> None:
                if status:
                    callback_errors.append(str(status))
                frames.append(bytes(data))

            with open_voice_capture_stream(
                sd, self.device_index, capture_audio
            ) as (sample_rate, fallback_note):
                if fallback_note:
                    self.signals.voice_status.emit(fallback_note)
                self.signals.voice_status.emit(
                    "듣는 중... 스페이스를 놓으면 전송합니다."
                )
                while not self.stop_event.wait(0.1):
                    if callback_errors:
                        raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")

            if callback_errors:
                raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")
            audio_data = b"".join(frames)
            if len(audio_data) < sample_rate:
                raise RuntimeError("녹음이 너무 짧습니다. 조금 더 길게 말씀해 주세요.")

            samples = process_voice_input(audio_data, sample_rate)

            self.signals.voice_status.emit("녹음한 음성을 로컬에서 인식하는 중입니다...")
            self.recognizer.transcribe(samples, self.signals)
        except Exception as error:
            self.signals.voice_failed.emit(f"{type(error).__name__}: {error}")


class VoiceConversationCaptureWorker(threading.Thread):
    def __init__(
        self,
        stop_event: threading.Event,
        recognizer: LocalSpeechRecognizer | WhisperSpeechRecognizer,
        signals: AppSignals,
        device_index: int | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.recognizer = recognizer
        self.signals = signals
        self.device_index = device_index

    def run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=128)
            callback_errors: list[str] = []

            def capture_audio(
                data: Any,
                _frame_count: int,
                _time_info: Any,
                status: Any,
            ) -> None:
                if status:
                    callback_errors.append(str(status))
                try:
                    audio_queue.put_nowait(bytes(data))
                except queue.Full:
                    callback_errors.append("마이크 입력 버퍼가 가득 찼습니다.")

            with open_voice_capture_stream(
                sd, self.device_index, capture_audio
            ) as (sample_rate, fallback_note):
                if fallback_note:
                    self.signals.voice_status.emit(fallback_note)
                self.signals.voice_status.emit(
                    "듣는 중... 편하게 말씀하시면 자동으로 전송합니다."
                )
                block_duration = 1024 / sample_rate
                preroll: deque[bytes] = deque(
                    maxlen=max(1, round(0.25 / block_duration))
                )
                onset: list[bytes] = []
                speech: list[bytes] = []
                onset_duration = 0.0
                speech_duration = 0.0
                voiced_duration = 0.0
                silence_duration = 0.0
                noise_floor = 0.0015
                speaking = False

                while not self.stop_event.is_set():
                    if callback_errors:
                        raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")
                    try:
                        audio_block = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    samples = np.frombuffer(audio_block, dtype=np.int16).astype(
                        np.float32
                    )
                    samples /= 32768.0
                    samples -= float(np.mean(samples))
                    rms = float(np.sqrt(np.mean(samples * samples)))
                    threshold = max(0.004, noise_floor * 2.5)
                    voiced = rms >= threshold
                    block_duration = len(audio_block) / 2 / sample_rate

                    if not speaking:
                        if voiced:
                            if not onset:
                                onset = list(preroll)
                                onset_duration = sum(
                                    len(block) / 2 / sample_rate for block in onset
                                )
                                voiced_duration = 0.0
                            onset.append(audio_block)
                            onset_duration += block_duration
                            voiced_duration += block_duration
                            if voiced_duration >= 0.06:
                                speech = onset
                                speech_duration = onset_duration
                                silence_duration = 0.0
                                speaking = True
                                onset = []
                                self.signals.voice_status.emit(
                                    "말씀을 듣고 있습니다..."
                                )
                        else:
                            noise_floor = min(
                                0.02,
                                noise_floor * 0.98 + rms * 0.02,
                            )
                            onset = []
                            onset_duration = 0.0
                            voiced_duration = 0.0
                            preroll.append(audio_block)
                        continue

                    speech.append(audio_block)
                    speech_duration += block_duration
                    if voiced:
                        voiced_duration += block_duration
                        silence_duration = 0.0
                    else:
                        silence_duration += block_duration

                    if (
                        silence_duration >= VOICE_CONVERSATION_END_SILENCE_SECONDS
                        or speech_duration >= 30
                    ):
                        if voiced_duration >= 0.25:
                            break
                        speech = []
                        speech_duration = 0.0
                        voiced_duration = 0.0
                        silence_duration = 0.0
                        speaking = False
                        preroll.clear()
                        self.signals.voice_status.emit(
                            "듣는 중... 편하게 말씀하시면 자동으로 전송합니다."
                        )

            if self.stop_event.is_set():
                return
            if callback_errors:
                raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")
            samples = process_voice_input(b"".join(speech), sample_rate)
            self.signals.voice_status.emit("음성을 로컬에서 인식하는 중입니다...")
            self.recognizer.transcribe(
                samples,
                self.signals,
                conversation=True,
                stop_event=self.stop_event,
            )
        except Exception as error:
            self.signals.conversation_voice_failed.emit(
                f"{type(error).__name__}: {error}"
            )


def process_voice_input(audio_data: bytes, sample_rate: int) -> Any:
    import numpy as np

    if sample_rate <= 0:
        raise ValueError("마이크 샘플링 속도는 0보다 커야 합니다.")
    samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    samples /= 32768.0
    if samples.size == 0:
        raise ValueError("녹음된 음성이 비어 있습니다.")
    if sample_rate != VOICE_RECOGNITION_SAMPLE_RATE:
        if sample_rate > VOICE_RECOGNITION_SAMPLE_RATE:
            target_cutoff = VOICE_RECOGNITION_SAMPLE_RATE * 0.45
            tap_count = max(31, round(161 * sample_rate / 48_000))
            if tap_count % 2 == 0:
                tap_count += 1
            offsets = np.arange(tap_count, dtype=np.float32) - tap_count // 2
            kernel = (
                2
                * target_cutoff
                / sample_rate
                * np.sinc(2 * target_cutoff / sample_rate * offsets)
            )
            kernel *= np.hanning(tap_count)
            kernel /= np.sum(kernel)
            padding = tap_count // 2
            padded_samples = np.pad(samples, (padding, padding), mode="edge")
            samples = np.convolve(
                padded_samples,
                kernel,
                mode="valid",
            ).astype(np.float32)
        target_count = round(
            len(samples) * VOICE_RECOGNITION_SAMPLE_RATE / sample_rate
        )
        target_positions = (
            np.arange(target_count) * sample_rate / VOICE_RECOGNITION_SAMPLE_RATE
        )
        samples = np.interp(
            target_positions,
            np.arange(len(samples)),
            samples,
        ).astype(np.float32)
    peak = float(np.max(np.abs(samples)))
    if 0 < peak < VOICE_INPUT_TARGET_PEAK:
        gain = min(VOICE_INPUT_MAX_GAIN, VOICE_INPUT_TARGET_PEAK / peak)
        samples *= gain
    return samples


class VoiceInputTestWorker(threading.Thread):
    def __init__(
        self,
        stop_event: threading.Event,
        signals: AppSignals,
        device_index: int | None,
    ) -> None:
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.signals = signals
        self.device_index = device_index

    def run(self) -> None:
        try:
            import sounddevice as sd

            frames: list[bytes] = []
            callback_errors: list[str] = []

            def capture_audio(
                data: Any,
                _frame_count: int,
                _time_info: Any,
                status: Any,
            ) -> None:
                if status:
                    callback_errors.append(str(status))
                frames.append(bytes(data))

            with open_voice_capture_stream(
                sd, self.device_index, capture_audio
            ) as (sample_rate, fallback_note):
                if fallback_note:
                    self.signals.voice_status.emit(fallback_note)
                while not self.stop_event.wait(0.1):
                    if callback_errors:
                        raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")

            if callback_errors:
                raise RuntimeError(f"마이크 입력 오류: {callback_errors[0]}")
            audio_data = b"".join(frames)
            if len(audio_data) < sample_rate * 2:
                raise RuntimeError("녹음이 너무 짧습니다. 1초 이상 녹음해 주세요.")
            self.signals.audio_test_ready.emit(
                process_voice_input(audio_data, sample_rate)
            )
        except Exception as error:
            self.signals.audio_test_failed.emit(
                f"{type(error).__name__}: {error}"
            )


class VoicePlaybackTestWorker(threading.Thread):
    def __init__(
        self,
        samples: Any,
        stop_event: threading.Event,
        signals: AppSignals,
    ) -> None:
        super().__init__(daemon=True)
        self.samples = samples
        self.stop_event = stop_event
        self.signals = signals

    def run(self) -> None:
        try:
            import sounddevice as sd

            sd.play(self.samples, VOICE_RECOGNITION_SAMPLE_RATE)
            while not self.stop_event.wait(0.1):
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    break
            if self.stop_event.is_set():
                sd.stop()
            sd.wait()
        except Exception as error:
            self.signals.audio_test_playback_failed.emit(
                f"{type(error).__name__}: {error}"
            )
            return
        self.signals.audio_test_playback_finished.emit()


def synthesize_cosmic_chime(sample_rate: int) -> Any:
    """별이 빛나는 듯한 짧은 반짝임 효과음(약 1.1초)을 합성한다.

    높은 음역의 유리 질감 종소리가 시간차로 차례로 번쩍이고, 배경에는 아주
    옅은 고역 실버(은빛) 섬광이 깔린 뒤 빠르게 사라진다.
    """
    import numpy as np

    duration = 1.1
    total = int(sample_rate * duration)
    t = np.arange(total, dtype=np.float64) / sample_rate

    # 1) 별 반짝임: 높은 음의 글린트(작은 종소리)가 차례로 번쩍인다.
    glints = np.zeros(total, dtype=np.float64)
    for start, frequency, amplitude in (
        (0.00, 1567.98, 0.46),   # G6
        (0.08, 2093.00, 0.40),   # C7
        (0.17, 2637.02, 0.32),   # E7
        (0.28, 3135.96, 0.26),   # G7
        (0.42, 4186.01, 0.18),   # C8
    ):
        local = t - start
        active = local >= 0
        if not np.any(active):
            continue
        local_active = local[active]
        attack = np.minimum(local_active / 0.006, 1.0)
        decay = np.exp(-local_active * 9.0)
        # 아주 가까운 두 주파수의 맥임으로 유리가 울리는 듯한 결을 만든다.
        beat = 0.5 * (
            np.sin(2 * np.pi * frequency * local_active)
            + np.sin(2 * np.pi * frequency * 1.004 * local_active)
        )
        glassy = 0.25 * np.sin(2 * np.pi * frequency * 2.76 * local_active)
        glints[active] += amplitude * attack * decay * (beat + glassy)

    # 2) 은빛 섬광 배경: 고역 노이즈를 좁게 통과시켜 옅게 깔아 준다.
    noise = np.random.default_rng(77).standard_normal(total)
    kernel = np.exp(-np.linspace(-3.0, 3.0, 65) ** 2)
    kernel = kernel / kernel.sum()
    noise = np.convolve(noise, kernel, mode="same")
    high_body = noise - np.convolve(noise, np.ones(33) / 33, mode="same")
    shimmer = high_body * 0.5 * np.sin(np.pi * np.clip(t / 0.9, 0.0, 1.0)) ** 2

    # 3) 은은한 중역 글로우: 별빛을 받쳐 주는 짧은 잔광.
    glow = (
        0.10 * np.sin(2 * np.pi * 783.99 * t)
        + 0.06 * np.sin(2 * np.pi * 1174.66 * t + 0.5)
    ) * np.exp(-t * 3.2)

    mixed = glints + shimmer + glow
    fade_out = np.clip((duration - t) / 0.22, 0.0, 1.0)
    mixed *= fade_out
    peak = float(np.max(np.abs(mixed)))
    if peak > 0:
        mixed = mixed / peak * 0.45
    return mixed.astype(np.float32)


def play_cosmic_chime_async() -> None:
    """답변 전송 알림 별빛 효과음을 별도 스트림으로 즉시 재생한다.

    sounddevice의 전역 재생(sd.play)을 쓰면 진행 중인 TTS 음성을 끊기 때문에,
    독립 OutputStream으로 재생해 TTS와 동시에 울려도 서로 방해하지 않는다.
    """

    def _play() -> None:
        try:
            import sounddevice as sd

            samples = synthesize_cosmic_chime(48_000).reshape(-1, 1)
            with sd.OutputStream(
                samplerate=48_000,
                channels=1,
                dtype="float32",
            ) as stream:
                stream.write(samples)
        except Exception:
            # 효과음 재생 실패는 대화에 영향을 주지 않는다.
            pass

    threading.Thread(
        target=_play,
        name="quasar-cosmic-chime",
        daemon=True,
    ).start()


EDGE_NETWORK_FAILURE_MARKERS = (
    "getaddrinfo",
    "_resolve_host",
    "Cannot connect to host",
    "Name or service not known",
    "No address associated with hostname",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "SSLError",
    "TimeoutError",
    "timed out",
)


def describe_speech_failure(details: str, returncode: int) -> str:
    """edge_tts 오류 원문을 사용자가 이해하기 쉬운 문장으로 바꾼다."""
    if any(marker in details for marker in EDGE_NETWORK_FAILURE_MARKERS):
        return (
            "인터넷에 연결할 수 없어 Edge 음성 합성에 실패했습니다. "
            "네트워크 연결 상태를 확인해 주세요."
        )
    if "NoAudioReceived" in details:
        return (
            "Edge 음성 서비스에서 오디오를 받지 못했습니다. "
            "잠시 후 다시 시도해 주세요."
        )
    return f"Edge 음성 합성에 실패했습니다: {details or returncode}"


def synthesize_with_windows_tts(
    text_path: Path,
    audio_path: Path,
    korean: bool,
) -> bool:
    """Windows 내장 TTS(SAPI)로 텍스트를 WAV 파일로 합성한다.

    Edge 음성 합성이 네트워크 문제 등으로 실패했을 때 쓰는 오프라인
    대체 수단이다. 한국어는 한국어 음성이 설치된 경우에만 합성한다.
    성공하면 True를 돌려준다.
    """
    korean_voice_setup = (
        "$voice = $synth.GetInstalledVoices() | "
        "Where-Object { $_.VoiceInfo.Culture.Name -like 'ko*' } | "
        "Select-Object -First 1; "
        "if (-not $voice) { $synth.Dispose(); exit 1 }; "
        "$synth.SelectVoice($voice.VoiceInfo.Name); "
        if korean
        else ""
    )
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        + korean_voice_setup
        + "$synth.SetOutputToWaveFile('"
        + str(audio_path).replace("'", "''")
        + "'); "
        + "$synth.Speak((Get-Content -Raw -Encoding UTF8 '"
        + str(text_path).replace("'", "''")
        + "')); "
        + "$synth.Dispose()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and audio_path.is_file()
        and audio_path.stat().st_size > 44
    )


class LocalSpeechOutputWorker(threading.Thread):
    def __init__(self, text: str, signals: AppSignals) -> None:
        super().__init__(daemon=True)
        self.text = text
        self.signals = signals
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            import numpy as np
            import av
            import sounddevice as sd

            contains_korean = any(
                "\uac00" <= character <= "\ud7a3" for character in self.text
            )
            voice = (
                "ko-KR-SunHiNeural"
                if contains_korean
                else "en-US-AriaNeural"
            )
            with tempfile.TemporaryDirectory(prefix="quasar-speech-") as temp_dir:
                text_path = Path(temp_dir) / "speech.txt"
                audio_path = Path(temp_dir) / "speech.mp3"
                text_path.write_text(self.text, encoding="utf-8")
                command = [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--file",
                    str(text_path),
                    "--voice",
                    voice,
                    "--write-media",
                    str(audio_path),
                    "--no-progress",
                ]
                speech_error: str | None = None
                for attempt in range(2):
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=90,
                        check=False,
                    )
                    if result.returncode == 0:
                        speech_error = None
                        break
                    details = result.stderr.strip() or result.stdout.strip()
                    speech_error = describe_speech_failure(
                        details, result.returncode
                    )
                    if attempt == 0 and (
                        "NoAudioReceived" in details
                        or any(
                            marker in details
                            for marker in EDGE_NETWORK_FAILURE_MARKERS
                        )
                    ):
                        time.sleep(1)
                        continue
                    break

                source_path = audio_path
                if speech_error is not None:
                    # 네트워크 문제 등으로 Edge 합성이 실패하면 Windows 내장
                    # TTS로 오프라인 재생을 시도한다.
                    fallback_audio_path = Path(temp_dir) / "speech_fallback.wav"
                    if synthesize_with_windows_tts(
                        text_path, fallback_audio_path, contains_korean
                    ):
                        source_path = fallback_audio_path
                    else:
                        raise RuntimeError(speech_error)

                with av.open(str(source_path)) as container:
                    audio_stream = container.streams.audio[0]
                    sample_rate = int(audio_stream.rate or 24_000)
                    resampler = av.AudioResampler(
                        format="fltp",
                        layout="mono",
                        rate=sample_rate,
                    )
                    frames = []
                    for frame in container.decode(audio_stream):
                        frames.extend(resampler.resample(frame))
                    frames.extend(resampler.resample(None))
                if not frames:
                    raise RuntimeError(
                        "Edge 음성 서비스에서 재생할 오디오를 받지 못했습니다."
                    )

                samples = np.concatenate(
                    [frame.to_ndarray().reshape(-1) for frame in frames]
                ).astype(np.float32)
                samples = np.tanh(samples * SPEECH_OUTPUT_GAIN) / math.tanh(
                    SPEECH_OUTPUT_GAIN
                )
                if not self.stop_event.is_set():
                    sd.play(samples, sample_rate)
                    while not self.stop_event.wait(0.05):
                        stream = sd.get_stream()
                        if stream is None or not stream.active:
                            break
                    if self.stop_event.is_set():
                        sd.stop()
        except Exception as error:
            self.signals.speech_failed.emit(
                self,
                f"{type(error).__name__}: {error}",
            )
            return
        self.signals.speech_finished.emit(self)


class ModelLoader(threading.Thread):
    def __init__(
        self,
        provider: str,
        client: Any,
        on_loaded: Any,
    ) -> None:
        super().__init__(daemon=True)
        self.provider = provider
        self.client = client
        self.on_loaded = on_loaded

    def run(self) -> None:
        model_names: set[str] = set()
        errors: list[str] = []
        try:
            model_names.update(
                model.id
                for model in self.client.models.list().data
                if self.provider in {"ollama", "nvidia_nim", "omni"}
                or model.id.startswith(("gpt-", "o1", "o3", "o4"))
            )
        except Exception as error:
            errors.append(f"모델 목록 조회 실패: {error}")

        if self.provider == "ollama":
            try:
                model_names.update(agent.list_ollama_cloud_models())
            except Exception as error:
                errors.append(f"Ollama Cloud 모델 목록 조회 실패: {error}")

        self.on_loaded(self.provider, model_names, errors)


class MessageInput(QPlainTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class DesktopControlDialog(QDialog):
    def __init__(
        self,
        control_session: DesktopControlSession,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.control_session = control_session
        self.setWindowTitle("Quasar 데스크톱 제어")
        self.setMinimumSize(720, 520)
        self.setModal(False)

        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        self.preview = QLabel("화면을 불러오는 중...")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.preview.setStyleSheet("background: #090d1c; border: 1px solid #303957;")
        layout.addWidget(self.preview, 1)

        self.status = QLabel("미리보기 전용 · 데스크톱 제어는 꺼져 있습니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.start_button = QPushButton()
        self.stop_button = QPushButton("제어 중지")
        self.stop_button.setEnabled(False)
        close_button = QPushButton("닫기")
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.start_button.clicked.connect(self._start_session)
        self.stop_button.clicked.connect(self._stop_session)
        close_button.clicked.connect(self.close)

        self._capture_exclusion_error: str | None = None
        self.update_session_duration()
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(250)
        self.preview_timer.timeout.connect(self._refresh_preview)
        self.preview_timer.start()
        self._refresh_preview()

    def update_session_duration(self) -> None:
        duration = self.control_session.duration_seconds
        self.notice.setText(
            "미리보기는 이 PC에서 초당 약 4회 갱신됩니다. 제어 세션을 시작하면 "
            f"최대 {duration}초 동안 최대 {MAX_ACTIONS_PER_SESSION}회 "
            "마우스·키 입력을 허용합니다. 화면은 에이전트가 캡처 도구를 호출할 때만 "
            "선택한 AI 제공자로 전송됩니다. 화면을 이해하려면 비전 입력을 지원하는 모델이 "
            "필요합니다. 암호나 비밀정보가 보이는 화면은 제어하지 마세요."
        )
        self.start_button.setText(f"제어 시작 (최대 {duration}초)")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self._capture_exclusion_error is not None:
            return
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            set_affinity = user32.SetWindowDisplayAffinity
            set_affinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            set_affinity.restype = ctypes.c_bool
            if not set_affinity(int(self.winId()), 0x00000011):
                error_code = ctypes.get_last_error()
                raise OSError(error_code, ctypes.FormatError(error_code))
        except (AttributeError, OSError) as error:
            self._capture_exclusion_error = str(error)
            self.status.setText(
                "알림: 화면 캡처에서 미리보기 창을 숨기지 못했습니다. "
                f"제어 시 화면에 미리보기 창이 포함될 수 있습니다 ({error})."
            )

    def _start_session(self) -> None:
        answer = QMessageBox.warning(
            self,
            "데스크톱 제어 활성화",
            "활성 세션 동안 에이전트가 화면을 관찰하고 마우스·키 입력을 수행할 수 있습니다. "
            f"세션은 최대 {self.control_session.duration_seconds}초 또는 "
            f"{MAX_ACTIONS_PER_SESSION}회 입력 후 자동으로 끝납니다. 시작할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.control_session.start()
        except RuntimeError as error:
            QMessageBox.warning(self, "데스크톱 제어를 시작할 수 없음", str(error))
            return
        self.status.setText(
            f"제어 활성 · 남은 시간 {self.control_session.remaining_seconds()}초 · "
            "중지 버튼으로 즉시 종료할 수 있습니다."
        )
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _stop_session(self) -> None:
        self.control_session.stop()
        self.status.setText("미리보기 전용 · 데스크톱 제어가 중지되었습니다.")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _refresh_preview(self) -> None:
        try:
            image = capture_desktop_image()
            image_data = image.tobytes("raw", "RGB")
            frame = QImage(
                image_data,
                image.width,
                image.height,
                image.width * 3,
                QImage.Format.Format_RGB888,
            ).copy()
            self.preview.setPixmap(
                QPixmap.fromImage(frame).scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except (OSError, RuntimeError) as error:
            self.preview.setText(f"화면 캡처 실패: {error}")
            self.preview.setPixmap(QPixmap())

        remaining = self.control_session.remaining_seconds()
        if remaining == 0 and self.stop_button.isEnabled():
            self.status.setText("세션이 만료되었습니다. 미리보기만 계속합니다.")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
        elif remaining > 0:
            self.status.setText(
                f"제어 활성 · 남은 시간 {remaining}초 · "
                "중지 버튼으로 즉시 종료할 수 있습니다."
            )
        elif self._capture_exclusion_error is not None:
            self.status.setText(
                "미리보기 전용 · 데스크톱 제어는 꺼져 있습니다. "
                "캡처 시 미리보기 창이 포함될 수 있습니다."
            )

    def closeEvent(self, event: Any) -> None:
        self.preview_timer.stop()
        self.control_session.stop()
        super().closeEvent(event)


class QuasarDesktopApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quasar")
        self.setWindowIcon(
            QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "quasar_icon.png"))
        )
        self.resize(1080, 720)
        self.setMinimumSize(760, 560)
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {BACKGROUND};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QWidget {{
                background: transparent;
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QWidget#mainCanvas {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: {CANVAS_BACKGROUND_STOPS[0][0]} {CANVAS_BACKGROUND_STOPS[0][1]},
                    stop: {CANVAS_BACKGROUND_STOPS[1][0]} {CANVAS_BACKGROUND_STOPS[1][1]},
                    stop: {CANVAS_BACKGROUND_STOPS[2][0]} {CANVAS_BACKGROUND_STOPS[2][1]}
                );
            }}
            QLabel {{ background: transparent; color: {TEXT}; }}
            QFrame#topBar {{
                background: #10172a;
                border: 1px solid #252d49;
                border-radius: 18px;
            }}
            QFrame#brandMark {{
                background: #1a1b37;
                border: 1px solid #38365f;
                border-radius: 14px;
            }}
            QFrame#assistantPanel {{
                background: #090d1c;
                border: 1px solid #222943;
                border-radius: 20px;
            }}
            QFrame#conversationPanel {{
                background: #0a0e1d;
                border: 1px solid #222943;
                border-radius: 20px;
            }}
            QFrame#composerPanel {{
                background: #10172a;
                border: 1px solid #303957;
                border-radius: 18px;
            }}
            QComboBox {{
                background: #171d32;
                color: {TEXT};
                border: 1px solid #303957;
                border-radius: 10px;
                padding: 9px 12px;
                min-height: 20px;
                selection-background-color: #484478;
            }}
            QComboBox::drop-down {{ border: 0; width: 24px; }}
            QComboBox:focus {{
                background: #1a2038;
                border: 1px solid {ACCENT};
            }}
            QComboBox QAbstractItemView {{
                background: #11182a;
                color: {TEXT};
                border: 1px solid #303957;
                selection-background-color: #34365e;
                outline: 0;
            }}
            QPlainTextEdit {{
                background: transparent;
                border: 0;
                padding: 8px 10px;
                color: {TEXT};
                selection-background-color: #484478;
            }}
            QPushButton {{
                background: #1b2038;
                color: #c7c2ff;
                border: 0;
                border-radius: 10px;
                padding: 9px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #292c4b; }}
            QPushButton:disabled {{ color: #777f9b; background: #171c2c; }}
            QPushButton#sendButton {{
                background: #7069d8;
                color: #ffffff;
                border-radius: 12px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton#sendButton:hover {{ background: #8179ed; }}
            QPushButton#sendButton:disabled {{ color: #b7b3db; background: #46446d; }}
            QScrollArea#chatScroll {{
                background: transparent;
                border: 0;
                border-radius: 20px;
            }}
            QWidget#chatContainer {{ background: transparent; }}
            QProgressBar {{
                background: #1b2035;
                border: 0;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
            """
        )

        self.settings = QSettings("Quasar", "Quasar")
        self.desktop_control_enabled = self.settings.value(
            "desktop/enabled",
            False,
            type=bool,
        )
        self.messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": build_system_prompt(getattr(self, "desktop_control_enabled", False)),
            }
        ]
        self.chat_history_store: ChatHistoryStore | None = None
        self.chat_history_error: str | None = None
        self.current_chat_id: str | None = None
        self.temporary_chat = False
        self.suspended_chat: tuple[str | None, list[dict[str, Any]]] | None = None
        history_directory = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
        )
        try:
            self.chat_history_store = ChatHistoryStore(
                history_directory / "chat_history.sqlite3"
            )
        except (OSError, sqlite3.Error) as error:
            self.chat_history_error = str(error)
        self.tool_rounds_error: str | None = None
        try:
            self.max_tool_rounds = agent.get_max_tool_rounds(
                self.settings.value("agent/max_tool_rounds", None, type=int)
            )
        except ValueError as error:
            self.max_tool_rounds = agent.DEFAULT_MAX_TOOL_ROUNDS
            self.tool_rounds_error = str(error)
        self.tool_calling_mode_error: str | None = None
        try:
            self.tool_calling_mode = agent.get_tool_calling_mode(
                self.settings.value("agent/tool_calling_mode", None, type=str)
            )
        except ValueError as error:
            self.tool_calling_mode = agent.quasar_tool_calling.LEGACY_MODE
            self.tool_calling_mode_error = str(error)
        self.office_monitor: agent.CliCollaborationMonitor | None = None
        self.office_monitor_client: Any = None
        self.office_monitor_source: _OfficeDocumentSource | None = None
        self.mcp_manager: agent.MCPServerManager | None = None
        self.mcp_startup_error: str | None = None
        self.live_analysis_server: agent.CliLiveAnalysisServer | None = None
        self.live_analysis_server_error: str | None = None
        self.microphone_notice: str | None = None
        self.microphone_device_index = self._restore_microphone_selection()
        self.busy = False
        self.worker_thread: threading.Thread | None = None
        self.closing = False
        self.request_cancel_pending = False
        self.request_cancel_event: threading.Event | None = None
        self.request_client: Any = None
        self.last_activity_message: str | None = None
        self.voice_engine = normalize_voice_engine(
            self.settings.value(
                "speech/recognition_engine",
                VOICE_ENGINE_NEMO,
                type=str,
            )
        )
        self.whisper_model = self.settings.value(
            "speech/whisper_model",
            WHISPER_DEFAULT_MODEL,
            type=str,
        )
        if self.whisper_model not in WHISPER_MODEL_CHOICES:
            self.whisper_model = WHISPER_DEFAULT_MODEL
        self.nim_api_key = self.settings.value(
            "speech/nim_api_key",
            os.getenv(VOICE_NIM_API_KEY_ENV, ""),
            type=str,
        )
        self.nim_api_key = (self.nim_api_key or "").strip()
        self.voice_recognizer = create_voice_recognizer(
            self.voice_engine,
            self.whisper_model,
            self.nim_api_key,
        )
        self.voice_transcript_correction_enabled = self.settings.value(
            "speech/fix_transcript",
            False,
            type=bool,
        )
        self.transcript_correction_worker: TranscriptCorrectionWorker | None = None
        self.pending_voice_audio_b64 = ""
        self.voice_recording = False
        self.voice_processing = False
        self.voice_stop_event: threading.Event | None = None
        self.voice_worker: VoiceCaptureWorker | None = None
        self.voice_conversation_active = False
        self.voice_conversation_listening = False
        self.voice_conversation_stop_event: threading.Event | None = None
        self.voice_conversation_worker: VoiceConversationCaptureWorker | None = None
        self.voice_conversation_retry_attempt = 0
        self.voice_conversation_retry_timer = QTimer(self)
        self.voice_conversation_retry_timer.setSingleShot(True)
        self.voice_conversation_retry_timer.timeout.connect(
            self._retry_voice_conversation
        )
        self.speech_worker: LocalSpeechOutputWorker | None = None
        self.pending_speech_messages: deque[str] = deque()
        self.pending_prompts: deque[tuple[str, str]] = deque()
        self.signals = AppSignals(self)
        self.signals.response_ready.connect(self._finish_with_response)
        self.signals.request_cancelled.connect(self._finish_cancelled_request)
        self.signals.request_failed.connect(self._finish_with_error)
        self.signals.request_progress.connect(self._show_request_progress)
        self.signals.agent_message.connect(self._show_agent_message)
        self.signals.alarm_triggered.connect(self._show_alarm_notification)
        self.signals.models_loaded.connect(self._apply_model_options)
        self.signals.voice_status.connect(self._show_voice_status)
        self.signals.voice_text_ready.connect(self._correct_voice_text)
        self.signals.voice_text_final.connect(self._submit_voice_text)
        self.signals.voice_audio_ready.connect(self._set_pending_voice_audio)
        self.signals.voice_failed.connect(self._finish_voice_error)
        self.signals.conversation_voice_text_ready.connect(
            self._correct_voice_conversation_text
        )
        self.signals.conversation_voice_text_final.connect(
            self._submit_voice_conversation_text
        )
        self.signals.conversation_voice_failed.connect(
            self._finish_voice_conversation_error
        )
        self.signals.speech_finished.connect(self._finish_speech)
        self.signals.speech_failed.connect(self._finish_speech_error)
        self.signals.tts_enabled_requested.connect(self._set_tts_enabled)
        self.signals.tool_approval_requested.connect(self._show_tool_approval)

        self.provider = os.getenv("AI_PROVIDER", "ollama").lower()
        self.client: Any = None
        self.model = ""
        self.startup_error: str | None = None
        self.original_alarm_notification_callback = (
            agent.alarm_notification_callback
        )
        self.original_tool_approval_callback = agent.tool_approval_callback
        self.alarm_notifications: list[QMessageBox] = []
        # 병렬 작업 창(TaskWindow)이 도구 메시지·승인을 자기 창으로 라우팅하도록
        # 요청을 실행하는 스레드마다 현재 세션을 기억한다.
        self._tool_context = threading.local()
        self.task_windows: list[TaskWindow] = []
        self._create_client(self.provider)
        self.original_run_tool = agent.run_tool
        self.desktop_control_settings_error: str | None = None
        self.desktop_control_seconds = self.settings.value(
            "desktop/control_session_seconds",
            DEFAULT_SESSION_SECONDS,
            type=int,
        )
        try:
            self.desktop_control_session = DesktopControlSession(
                self.desktop_control_seconds
            )
        except ValueError as error:
            self.desktop_control_seconds = DEFAULT_SESSION_SECONDS
            self.desktop_control_session = DesktopControlSession()
            self.desktop_control_settings_error = str(error)
        self.desktop_tool_schemas = [
            agent.tool_schema(
                "desktop_start_session",
                "Request user approval to start the time-limited desktop control session. "
                "Call this before the first desktop tool whenever the session is not active, "
                "or right after any desktop tool reports an inactive or expired session. "
                "The user sees an approval prompt; on approval the session starts "
                "immediately and desktop tools become usable.",
                {},
                [],
            ),
            agent.tool_schema(
                "desktop_capture",
                "Capture the current Windows virtual desktop as an image for visual analysis. "
                "Only works while the user-started desktop control session is active. "
                "Subsequent mouse tools use x and y normalized independently from 0 to 1000 "
                "across the original image, regardless of image resizing.",
                {},
                [],
            ),
            agent.tool_schema(
                "desktop_move_mouse",
                "Move the mouse to normalized coordinates in the latest desktop_capture image. "
                "x and y each range from 0 to 1000 across the original image; use this scale "
                "even if the image is resized for vision. Only works during the active session.",
                {
                    "x": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Horizontal position from 0 (left) to 1000 (right).",
                    },
                    "y": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Vertical position from 0 (top) to 1000 (bottom).",
                    },
                },
                ["x", "y"],
            ),
            agent.tool_schema(
                "desktop_click",
                "Move to normalized coordinates in the latest desktop_capture image and "
                "click. x and y each range from 0 to 1000 across the original image; use "
                "this scale even if the image is resized for vision. Optional button is "
                "left (default), right, or middle; set double to true for a double click. "
                "Do not use for irreversible actions without explicit user confirmation.",
                {
                    "x": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Horizontal position from 0 (left) to 1000 (right).",
                    },
                    "y": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Vertical position from 0 (top) to 1000 (bottom).",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button to click. Defaults to left.",
                    },
                    "double": {
                        "type": "boolean",
                        "description": "Set true to perform a double click. Defaults to false.",
                    },
                },
                ["x", "y"],
            ),
            agent.tool_schema(
                "desktop_type_text",
                "Type literal text into the currently focused desktop field. "
                "Never enter passwords, secrets, or text the user has not authorized.",
                {"text": {"type": "string"}},
                ["text"],
            ),
            agent.tool_schema(
                "desktop_press_key",
                "Press one allowed desktop key once. Allowed: navigation keys (ENTER, TAB, "
                "ESCAPE, BACKSPACE, DELETE, SPACE, UP, DOWN, LEFT, RIGHT, HOME, END, "
                "PAGEUP, PAGEDOWN, INSERT), modifiers (SHIFT, CONTROL, ALT, LWIN), function "
                "keys (F1-F12), PRINTSCREEN, NUMLOCK, SCROLLLOCK, APPS (context-menu key), "
                "media keys (VOLUME_MUTE, VOLUME_DOWN, VOLUME_UP, MEDIA_NEXT_TRACK, "
                "MEDIA_PREV_TRACK, MEDIA_PLAY_PAUSE), and browser keys (BROWSER_BACK, "
                "BROWSER_FORWARD). For held-down combinations such as CTRL+C use "
                "desktop_press_keys instead.",
                {
                    "key": {
                        "type": "string",
                        "enum": sorted(ALLOWED_DESKTOP_KEYS),
                    }
                },
                ["key"],
            ),
            agent.tool_schema(
                "desktop_press_keys",
                "Press a desktop key combination, such as CONTROL+C, ALT+TAB, or LWIN+E. "
                "Provide 1-4 key names using the same names as desktop_press_key; they are "
                "held down together and released in reverse order.",
                {
                    "keys": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(ALLOWED_DESKTOP_KEYS),
                        },
                        "minItems": 1,
                        "maxItems": 4,
                        "description": "Key names pressed together, in order.",
                    }
                },
                ["keys"],
            ),
            agent.tool_schema(
                "desktop_scroll",
                "Scroll with the mouse wheel, optionally after moving to normalized "
                "coordinates in the latest desktop_capture image. direction is up, down, "
                "left, or right; clicks are wheel notches from 1 to 10. Omit x and y to "
                "scroll at the current cursor position.",
                {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                    },
                    "clicks": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Wheel notches to scroll. Defaults to 3.",
                    },
                    "x": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Optional horizontal position (0-1000).",
                    },
                    "y": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Optional vertical position (0-1000).",
                    },
                },
                ["direction"],
            ),
            agent.tool_schema(
                "desktop_drag",
                "Hold the left mouse button at start coordinates, drag to end coordinates, "
                "and release there. All coordinates are normalized 0-1000 against the "
                "latest desktop_capture image. Use for moving items, sliders, or text "
                "selection. Do not use for irreversible actions without explicit user "
                "confirmation.",
                {
                    "x1": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Start horizontal position.",
                    },
                    "y1": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "Start vertical position.",
                    },
                    "x2": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "End horizontal position.",
                    },
                    "y2": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                        "description": "End vertical position.",
                    },
                },
                ["x1", "y1", "x2", "y2"],
            ),
        ]
        self._sync_desktop_tools()
        self.tts_tool_schema = agent.tool_schema(
            "set_tts_enabled",
            "Turn Quasar's spoken responses on or off. Use when the user asks to enable or disable TTS.",
            {"enabled": {"type": "boolean"}},
            ["enabled"],
        )
        agent.TOOLS.append(self.tts_tool_schema)
        self._configure_agent()
        self._build_ui()
        self._refresh_status()
        if self.client is not None:
            self._load_model_options()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("mainCanvas")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(30, 22, 30, 20)
        layout.setSpacing(12)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        header = QHBoxLayout(top_bar)
        header.setContentsMargins(10, 7, 10, 7)
        header.setSpacing(12)

        brand_mark = QFrame()
        brand_mark.setObjectName("brandMark")
        brand_mark.setFixedSize(44, 44)
        mark_layout = QVBoxLayout(brand_mark)
        mark_layout.setContentsMargins(0, 0, 0, 0)
        mark = QLabel("Q")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"color: {ACCENT}; font-size: 21px; font-weight: 800;")
        mark_layout.addWidget(mark)
        header.addWidget(brand_mark)

        self.menu_button = QToolButton()
        self.menu_button.setText("☰")
        self.menu_button.setFixedSize(46, 46)
        self.menu_button.setToolTip("Quasar 메뉴")
        self.menu_button.setAccessibleName("Quasar 메뉴 열기")
        self.menu_button.setStyleSheet(
            "QToolButton { font-size: 23px; font-weight: 700; padding: 0; }"
        )
        self.menu_button.clicked.connect(self._toggle_radial_menu)

        self.new_chat_button = QPushButton("새 채팅")
        self.new_chat_button.setFixedHeight(40)
        self.new_chat_button.setToolTip("새 채팅 시작")
        self.new_chat_button.clicked.connect(self._start_new_chat)

        self.new_task_button = QPushButton("새 작업 창")
        self.new_task_button.setFixedHeight(40)
        self.new_task_button.setToolTip(
            "현재 작업과 별개로 실행되는 새 작업 창을 엽니다.\n"
            "창마다 따로 요청을 보내 여러 작업을 동시에 진행할 수 있습니다."
        )
        self.new_task_button.clicked.connect(self._open_task_window)

        self.provider_selector = QComboBox()
        self.provider_selector.addItems(("ollama", "openai", "nvidia_nim", "omni"))
        self.provider_selector.setCurrentText(self.provider)
        self.provider_selector.currentTextChanged.connect(self._change_provider)
        self.provider_selector.setToolTip("AI 공급자 선택")
        self.provider_selector.setMinimumWidth(0)
        self.provider_selector.setFixedSize(112, 40)
        header.addWidget(self.provider_selector)

        self.model_selector = QComboBox()
        self.model_selector.setEditable(True)
        self.model_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_selector.setMinimumWidth(160)
        self.model_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.model_selector.setFixedHeight(40)
        self.model_selector.setEditText(self.model)
        self.model_selector.setToolTip("모델 선택 또는 모델 이름 직접 입력")
        self.model_selector.currentTextChanged.connect(self._remember_model)
        header.addWidget(self.model_selector, 1)
        header.addWidget(self.new_task_button)
        header.addWidget(self.new_chat_button)
        header.addWidget(self.menu_button)

        self.refresh_models_button = QPushButton("모델 새로고침")
        self.refresh_models_button.setMinimumHeight(40)
        self.refresh_models_button.clicked.connect(self._load_model_options)

        self.checklist_button = QPushButton("체크리스트 열기")
        self.checklist_button.setMinimumHeight(40)
        self.checklist_button.setCheckable(True)
        self.checklist_button.toggled.connect(self._set_checklist_visible)
        self.checklist_button.hide()
        layout.addWidget(top_bar)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(f"color: {MUTED}; font-size: 12px; padding-left: 4px;")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.chat_container = QWidget()
        self.chat_container.setObjectName("chatContainer")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(20, 18, 20, 18)
        self.chat_layout.setSpacing(18)

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(12, 12, 12, 12)
        empty_layout.addStretch(1)
        welcome_mark = QLabel("✦")
        welcome_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_mark.setStyleSheet(
            "color: #82b8ac; font-size: 30px; font-weight: 400;"
        )
        welcome_title = QLabel("무엇을 도와드릴까요?")
        welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_title.setStyleSheet("font-size: 21px; font-weight: 700;")
        welcome_copy = QLabel(
            "질문을 입력하면 Quasar가 함께 해결해 드릴게요."
        )
        welcome_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_copy.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        empty_layout.addWidget(welcome_mark)
        empty_layout.addSpacing(8)
        empty_layout.addWidget(welcome_title)
        empty_layout.addWidget(welcome_copy)
        empty_layout.addStretch(1)
        self.chat_layout.addWidget(self.empty_state, 1)
        self.chat_layout.addStretch(1)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setWidget(self.chat_container)
        self.chat_scroll.verticalScrollBar().rangeChanged.connect(
            self._scroll_chat_to_bottom
        )

        conversation_row = QHBoxLayout()
        conversation_row.setSpacing(16)

        assistant_panel = QFrame()
        assistant_panel.setObjectName("assistantPanel")
        assistant_panel.setFixedWidth(164)
        orb_column = QVBoxLayout()
        orb_column.setContentsMargins(10, 20, 10, 18)
        orb_column.setSpacing(8)
        orb_column.addStretch(1)
        self.orb_column = orb_column
        self.orb = OrbWidget()
        orb_column.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)
        orb_label = QLabel("QUASAR")
        orb_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        orb_label.setStyleSheet("font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        orb_column.addWidget(orb_label)
        orb_caption = QLabel("AI ASSISTANT")
        orb_caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        orb_caption.setStyleSheet(
            f"color: {MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 1px;"
        )
        orb_column.addWidget(orb_caption)
        orb_column.addStretch(1)
        assistant_panel.setLayout(orb_column)
        conversation_row.addWidget(assistant_panel)

        conversation_panel = QFrame()
        conversation_panel.setObjectName("conversationPanel")
        conversation_layout = QVBoxLayout(conversation_panel)
        conversation_layout.setContentsMargins(1, 1, 1, 1)
        conversation_layout.addWidget(self.chat_scroll)
        conversation_row.addWidget(conversation_panel, 1)

        self.checklist_panel = QFrame()
        self.checklist_panel.setObjectName("assistantPanel")
        self.checklist_panel.setFixedWidth(280)
        checklist_layout = QVBoxLayout(self.checklist_panel)
        checklist_layout.setContentsMargins(14, 14, 14, 14)
        checklist_header = QHBoxLayout()
        checklist_title = QLabel("체크리스트")
        checklist_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        checklist_header.addWidget(checklist_title)
        checklist_header.addStretch(1)
        self.checklist_refresh_button = QPushButton("새로고침")
        self.checklist_refresh_button.clicked.connect(self._refresh_checklist)
        checklist_header.addWidget(self.checklist_refresh_button)
        checklist_layout.addLayout(checklist_header)
        self.checklist_status = QLabel()
        self.checklist_status.setWordWrap(True)
        self.checklist_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        checklist_layout.addWidget(self.checklist_status)
        self.checklist_list = QListWidget()
        self.checklist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.checklist_list.itemChanged.connect(self._set_checklist_item_completed)
        checklist_layout.addWidget(self.checklist_list, 1)
        self.checklist_panel.hide()
        conversation_row.addWidget(self.checklist_panel)
        layout.addLayout(conversation_row, 1)

        composer_row = QHBoxLayout()
        composer_row.setContentsMargins(0, 0, 0, 0)
        composer_row.setSpacing(12)
        composer_panel = QFrame()
        composer_panel.setObjectName("composerPanel")
        composer_layout = QHBoxLayout(composer_panel)
        composer_layout.setContentsMargins(10, 6, 8, 6)
        self.composer = MessageInput()
        self.composer.setPlaceholderText(
            "메시지 또는 / 명령어를 입력하세요 · 작업 중 Enter: 다음 작업 대기열 추가"
        )
        self.composer.setToolTip(
            "Enter: 전송 · Shift+Enter: 줄바꿈\n"
            "작업이 실행 중일 때 Enter를 누르면 다음 작업이 대기열에 추가되고, "
            "현재 작업이 끝난 뒤 순서대로 실행됩니다."
        )
        self.composer.setFixedHeight(58)
        self.composer.send_requested.connect(self.submit)
        composer_layout.addWidget(self.composer, 1)

        self.send_button = QPushButton("전송")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(96, 44)
        self.send_button.clicked.connect(self._handle_send_button)
        composer_layout.addWidget(self.send_button)
        composer_row.addWidget(composer_panel, 1)

        self.voice_button = VoicePushButton("누르고 말하기")
        self.voice_button.setFixedSize(116, 44)
        self.voice_button.setToolTip(
            "마이크 버튼 또는 Space를 누르고 말한 뒤, 놓으면 인식된 음성을 전송합니다."
        )
        self.voice_button.hold_started.connect(self._start_voice_recording)
        self.voice_button.hold_ended.connect(self._stop_voice_recording)
        composer_row.addWidget(self.voice_button)
        layout.addLayout(composer_row)

        hint_row = QHBoxLayout()
        self.voice_response_toggle = QCheckBox("답변/공동작업 음성")
        self.voice_response_toggle.setChecked(
            self.settings.value("voice/tts_enabled", True, type=bool)
        )
        self.voice_response_toggle.setToolTip(
            "AI 답변과 공동작업 질문·수정 제안 음성을 켜거나 끕니다."
        )
        self.voice_response_toggle.toggled.connect(self._set_tts_enabled)
        hint_row.addWidget(self.voice_response_toggle)
        hint_row.addStretch(1)
        self.composer_hint = QLabel(
            "Space 길게 누르기: 말하기  ·  Enter: 전송  ·  "
            "작업 중 Enter: 다음 작업  ·  /help: 명령어 목록"
        )
        self.composer_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.composer_hint.setStyleSheet(f"color: {MUTED}; font-size: 10px; padding-right: 4px;")
        hint_row.addWidget(self.composer_hint)
        layout.addLayout(hint_row)
        self.black_hole_overlay = BlackHoleOverlay(central)
        self.black_hole_overlay.setGeometry(central.rect())
        self.galaxy_transition = GalaxyTransitionOverlay(
            central,
            self.orb,
            self.orb_column,
        )
        self.orb.clicked.connect(self.galaxy_transition.toggle)
        self.galaxy_transition.expanded_changed.connect(
            self._set_voice_conversation_active
        )
        self.control_overlay = RadialMenuOverlay(central)
        self.control_overlay.setObjectName("radialControlOverlay")
        self.control_overlay.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.control_overlay.setStyleSheet(
            "QWidget#radialControlOverlay { background: rgba(3, 5, 14, 205); }"
        )
        self.control_overlay.dismissed.connect(self._close_radial_menu)
        self.control_ring = QFrame(self.control_overlay)
        self.control_ring.setObjectName("controlRing")
        self.control_ring.setStyleSheet(
            "QFrame#controlRing {"
            "background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.72,"
            "stop: 0 #11182a, stop: 0.68 #11182a, stop: 1 #242343);"
            "border: 1px solid #44406e; border-radius: 260px; }"
        )
        self.control_core = QFrame(self.control_ring)
        self.control_core.setObjectName("controlCore")
        self.control_core.setStyleSheet(
            "QFrame#controlCore { background: #090d1c; border: 1px solid #303957;"
            "border-radius: 120px; }"
        )
        core_layout = QVBoxLayout(self.control_core)
        core_layout.setContentsMargins(24, 20, 24, 20)
        core_layout.setSpacing(8)
        self.control_core_title = QLabel("QUASAR")
        self.control_core_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.control_core_title.setStyleSheet(
            f"color: {ACCENT}; font-size: 14px; font-weight: 800; letter-spacing: 2px;"
        )
        core_layout.addWidget(self.control_core_title)
        self.control_core_close_button: QToolButton | None = None
        self.radial_buttons: list[QToolButton] = []
        radial_actions = (
            ("새 채팅", self._start_new_chat),
            ("새 작업 창", self._open_task_window),
            ("채팅 목록", self._open_recent_chats),
            ("화면 제어", self._open_desktop_control),
            ("임시 채팅", self._toggle_temporary_chat),
            ("모델 갱신", self._load_model_options),
            ("설정", self._open_settings),
            ("LSP 연결", self._show_lsp_connection),
            ("문서 감시", self._open_office_watch_dialog),
            ("체크리스트", self.checklist_button.toggle),
        )
        self.temporary_chat_button: QToolButton | None = None
        self.model_refresh_radial_button: QToolButton | None = None
        for label, callback in radial_actions:
            button = QToolButton(self.control_overlay)
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFixedSize(108, 46)
            button.setStyleSheet(
                "QToolButton { background: #222541; border: 1px solid #39375f;"
                "border-radius: 15px; color: #e7eaff; font-weight: 650; }"
                "QToolButton:hover { background: #37345f; border-color: #9b91ff; }"
            )
            button.clicked.connect(self._close_radial_menu)
            button.clicked.connect(callback)
            self.radial_buttons.append(button)
            if label == "임시 채팅":
                self.temporary_chat_button = button
            elif label == "모델 갱신":
                self.model_refresh_radial_button = button
        close_button = QToolButton(self.control_core)
        close_button.setText("닫기")
        close_button.setFixedHeight(28)
        close_button.setStyleSheet(
            "QToolButton { color: #a9a7c8; background: transparent; border: 0; }"
            "QToolButton:hover { color: #ffffff; }"
        )
        close_button.clicked.connect(self._close_radial_menu)
        core_layout.addWidget(close_button)
        self.control_core_close_button = close_button
        self.radial_starburst = RadialStarburstOverlay(self.control_overlay)
        self.radial_starburst.raise_()
        self.radial_animation_progress = 0.0
        self.radial_animation_from_progress = 0.0
        self.radial_animation_to_progress = 0.0
        self.radial_animation_opening = True
        self.radial_animation_started_at = 0.0
        self.radial_animation_duration_ms = 480
        self.radial_animation_timer = QTimer(self)
        self.radial_animation_timer.setInterval(16)
        self.radial_animation_timer.timeout.connect(
            self._advance_radial_animation
        )
        self.control_overlay.hide()
        self._layout_radial_menu()
        top_bar.setFixedHeight(60)
        self.voice_interrupt_shortcut = QShortcut(
            QKeySequence("Ctrl+Space"),
            self,
        )
        self.voice_interrupt_shortcut.setContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.voice_interrupt_shortcut.setEnabled(False)
        self.voice_interrupt_shortcut.activated.connect(
            self._interrupt_voice_conversation
        )
        self.office_monitor_timer = QTimer(self)
        self.office_monitor_timer.setInterval(500)
        self.office_monitor_timer.timeout.connect(self._poll_office_monitor)
        self.office_monitor_timer.start()
        self.voice_button.setFocus()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "black_hole_overlay"):
            self.black_hole_overlay.setGeometry(self.centralWidget().rect())
        if hasattr(self, "galaxy_transition"):
            self.galaxy_transition.setGeometry(self.centralWidget().rect())
        if hasattr(self, "control_overlay"):
            self._layout_radial_menu()

    def _radial_menu_origin(self) -> QPointF:
        orb_center = self.orb.mapToGlobal(self.orb.rect().center())
        overlay_position = self.control_overlay.mapFromGlobal(orb_center)
        return QPointF(overlay_position)

    def _layout_radial_menu(self) -> None:
        central = self.centralWidget()
        if central is None or not hasattr(self, "control_overlay"):
            return
        bounds = central.rect()
        self.control_overlay.setGeometry(bounds)
        self.radial_starburst.setGeometry(bounds)
        backdrop_alpha = round(100 * self.radial_animation_progress)
        self.control_overlay.setStyleSheet(
            "QWidget#radialControlOverlay {"
            f"background: rgba(3, 5, 14, {backdrop_alpha});"
            "}"
        )
        diameter = max(420, min(620, min(bounds.width(), bounds.height()) - 48))
        ring_x = (bounds.width() - diameter) // 2
        ring_y = (bounds.height() - diameter) // 2
        self.control_ring.setGeometry(ring_x, ring_y, diameter, diameter)

        core_size = round(diameter * 0.46)
        core_x = (diameter - core_size) // 2
        core_y = (diameter - core_size) // 2
        self.control_core.setGeometry(core_x, core_y, core_size, core_size)
        self.control_ring.setStyleSheet(
            "QFrame#controlRing {"
            "background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.72,"
            f"stop: 0 rgba(17, 24, 42, {round(255 * self.radial_animation_progress)}), "
            f"stop: 0.68 rgba(17, 24, 42, {round(255 * self.radial_animation_progress)}), "
            f"stop: 1 rgba(36, 35, 67, {round(255 * self.radial_animation_progress)}));"
            f"border: 1px solid rgba(68, 64, 110, "
            f"{round(255 * self.radial_animation_progress)});"
            f"border-radius: {diameter // 2}px;"
            "}"
        )
        core_alpha = round(255 * self.radial_animation_progress)
        self.control_core.setStyleSheet(
            "QFrame#controlCore { "
            f"background: rgba(9, 13, 28, {core_alpha}); "
            f"border: 1px solid rgba(48, 57, 87, {core_alpha});"
            f"border-radius: {core_size // 2}px;"
            "}"
        )
        self.control_core_title.setStyleSheet(
            "font-size: 14px; font-weight: 800; letter-spacing: 2px;"
            f"color: rgba(155, 145, 255, {core_alpha});"
        )
        if self.control_core_close_button is not None:
            self.control_core_close_button.setStyleSheet(
                "QToolButton { "
                f"color: rgba(169, 167, 200, {core_alpha}); "
                "background: transparent; border: 0; }"
                "QToolButton:hover { color: #ffffff; }"
            )
        center = diameter / 2
        origin = self._radial_menu_origin()
        radius = diameter * 0.365
        button_width = self.radial_buttons[0].width()
        button_height = self.radial_buttons[0].height()
        targets = []
        for index, button in enumerate(self.radial_buttons):
            angle = math.radians(-90 + index * 45)
            button_x = round(
                center + radius * math.cos(angle) - button_width / 2
            )
            button_y = round(
                center + radius * math.sin(angle) - button_height / 2
            )
            targets.append(
                QPointF(
                    ring_x + button_x + button_width / 2,
                    ring_y + button_y + button_height / 2,
                )
            )
            delay = (index % 4) * 0.055
            button_progress = max(
                0.0,
                min(
                    1.0,
                    (self.radial_animation_progress - delay) / (1 - delay),
                ),
            )
            eased_progress = button_progress * button_progress * (
                3 - 2 * button_progress
            )
            current_x = round(
                origin.x()
                + (ring_x + button_x - origin.x()) * eased_progress
            )
            current_y = round(
                origin.y()
                + (ring_y + button_y - origin.y()) * eased_progress
            )
            button.move(current_x, current_y)
            button_alpha = round(255 * eased_progress)
            button.setStyleSheet(
                "QToolButton { "
                f"background: rgba(34, 37, 65, {button_alpha}); "
                f"border: 1px solid rgba(57, 55, 95, {button_alpha}); "
                "border-radius: 15px; "
                f"color: rgba(231, 234, 255, {button_alpha}); "
                "font-weight: 650; }"
                "QToolButton:hover { background: rgba(55, 52, 95, 255); "
                "border-color: #9b91ff; }"
            )
        self.radial_starburst.set_animation_state(
            self.radial_animation_progress,
            origin,
            targets,
        )
    def _create_client(self, provider: str) -> None:
        try:
            if (
                provider in {"nvidia_nim", "omni"}
                and not os.getenv("NVIDIA_API_KEY", "").strip()
                and getattr(self, "nim_api_key", "").strip()
            ):
                # 음성 인식에서 쓰던 NVIDIA API 키를 채팅 공급자로도 재사용한다.
                # omni 공급자는 NVIDIA_API_KEY를 대체 키로도 받는다.
                os.environ["NVIDIA_API_KEY"] = self.nim_api_key.strip()
            self.client, self.model = agent.create_client(provider)
            self.provider = provider
            self.startup_error = None
            self._start_model_warmup()
        except Exception as error:
            self.client = None
            self.model = ""
            self.provider = provider
            self.startup_error = str(error)

    def _start_model_warmup(self) -> None:
        """첫 대화 전에 로컬 모델을 메모리에 미리 올려 둔다.

        ollama 같은 로컬 공급자는 첫 요청 때 모델을 로드하는 데 수십 초가 걸리고,
        5분간 사용하지 않으면 다시 내렸다가 다음 요청 때 또 로드한다. 앱 시작 직후와
        4분마다 1토큰짜리 요청을 보내 모델을 항상 준비 상태로 유지한다.
        """
        if self.client is None or not self.model:
            return
        provider = (self.provider or "").lower()
        if "ollama" not in provider:
            # 클라우드 공급자는 모델 로드 지연이 없어서 예열이 필요 없다.
            return
        previous_stop = getattr(self, "_model_warmup_stop", None)
        previous_model = getattr(self, "_model_warmup_model", None)
        if (
            previous_stop is not None
            and not previous_stop.is_set()
            and previous_model == self.model
        ):
            return
        if previous_stop is not None:
            previous_stop.set()
        stop_event = threading.Event()
        self._model_warmup_stop = stop_event
        self._model_warmup_model = self.model
        threading.Thread(
            target=self._keep_model_warm,
            args=(self.client, self.model, stop_event),
            name="quasar-model-warmup",
            daemon=True,
        ).start()

    def _keep_model_warm(
        self,
        client: Any,
        model: str,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set() and not self.closing:
            try:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    timeout=60,
                )
            except Exception:
                # 모델 로드 전 실패 등은 다음 주기에 다시 시도한다.
                pass
            stop_event.wait(240.0)

    def _change_provider(self, provider: str) -> None:
        if self.busy or provider == self.provider:
            return
        office_monitor_was_enabled = self._reset_office_monitor()
        self._create_client(provider)
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        self.model_selector.setEditText(self.model)
        self.model_selector.blockSignals(False)
        self.refresh_models_button.setEnabled(self.client is not None)
        if self.model_refresh_radial_button is not None:
            self.model_refresh_radial_button.setEnabled(self.client is not None)
        self._refresh_status()
        if office_monitor_was_enabled:
            self.status_label.setText(
                "AI 공급자가 변경되어 문서 감시를 중지했습니다. 다시 켜 주세요."
            )
        if self.client is not None:
            self._load_model_options()

    def _remember_model(self, model: str) -> None:
        model = model.strip()
        if model:
            if model != self.model and self._reset_office_monitor():
                self.status_label.setText(
                    "AI 모델이 변경되어 문서 감시를 중지했습니다. 다시 켜 주세요."
                )
            self.model = model
            self._start_model_warmup()

    def _restore_microphone_selection(self) -> int | None:
        saved_name = self.settings.value(
            "audio/input_device_name",
            "",
            type=str,
        )
        saved_host_api = self.settings.value(
            "audio/input_device_host_api",
            -1,
            type=int,
        )
        saved_input_channels = self.settings.value(
            "audio/input_device_channels",
            -1,
            type=int,
        )
        saved_index = self.settings.value("audio/input_device", -1, type=int)
        if not saved_name and saved_index < 0:
            return None

        try:
            import sounddevice as sd
        except ImportError as error:
            self.microphone_notice = f"마이크 설정을 불러오지 못했습니다: {error}"
            return None

        try:
            devices = sd.query_devices()
        except sd.PortAudioError as error:
            self.microphone_notice = f"마이크 목록을 불러오지 못했습니다: {error}"
            return None

        if saved_name and saved_host_api >= 0:
            device_index = find_input_device_index(
                devices,
                saved_name,
                saved_host_api,
                saved_input_channels if saved_input_channels > 0 else None,
            )
            if device_index is not None:
                return device_index
            self.microphone_notice = (
                "저장된 마이크를 찾지 못해 시스템 기본 마이크를 사용합니다. "
                "설정에서 입력 장치를 확인해 주세요."
            )
            return None

        if saved_index < len(devices) and saved_index >= 0:
            device = devices[saved_index]
            if int(device["max_input_channels"]) > 0:
                self.settings.setValue("audio/input_device_name", device["name"])
                self.settings.setValue(
                    "audio/input_device_host_api",
                    int(device["hostapi"]),
                )
                self.settings.setValue(
                    "audio/input_device_channels",
                    int(device["max_input_channels"]),
                )
                self.settings.sync()
                return saved_index

        self.settings.setValue("audio/input_device", -1)
        self.settings.sync()
        self.microphone_notice = (
            "저장된 장치가 마이크가 아니어서 시스템 기본 마이크를 사용합니다. "
            "설정에서 입력 장치를 확인해 주세요."
        )
        return None

    def _sync_desktop_tools(self) -> None:
        """설정 상태에 따라 데스크톱 제어 도구 스키마를 TOOLS에 넣거나 뺀다."""
        desktop_names = {
            schema["function"]["name"]
            for schema in self.desktop_tool_schemas
            if isinstance(schema, dict)
            and isinstance(schema.get("function"), dict)
        }
        agent.TOOLS[:] = [
            tool
            for tool in agent.TOOLS
            if not (
                isinstance(tool, dict)
                and isinstance(tool.get("function"), dict)
                and tool["function"].get("name") in desktop_names
            )
        ]
        if self.desktop_control_enabled:
            agent.TOOLS.extend(self.desktop_tool_schemas)

    def _update_system_prompt(self) -> None:
        prompt = build_system_prompt(getattr(self, "desktop_control_enabled", False))
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = prompt
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})

    def _open_settings(self) -> None:
        dialog = MicrophoneSettingsDialog(
            self.microphone_device_index,
            self.max_tool_rounds,
            self.tool_calling_mode,
            self.desktop_control_seconds,
            self.voice_engine,
            self.whisper_model,
            self.voice_transcript_correction_enabled,
            self.nim_api_key,
            self.desktop_control_enabled,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_device_index = dialog.selected_device_index
        selected_device: Any = None
        if selected_device_index is not None:
            try:
                import sounddevice as sd
            except ImportError as error:
                QMessageBox.warning(
                    self,
                    "마이크 설정 실패",
                    f"마이크 기능을 사용할 수 없습니다: {error}",
                )
                return
            try:
                devices = sd.query_devices()
            except sd.PortAudioError as error:
                QMessageBox.warning(
                    self,
                    "마이크 설정 실패",
                    f"마이크 목록을 불러오지 못했습니다: {error}",
                )
                return
            if selected_device_index >= len(devices):
                QMessageBox.warning(
                    self,
                    "마이크 설정 실패",
                    "선택한 마이크를 찾을 수 없습니다. 목록을 새로고침해 주세요.",
                )
                return
            selected_device = devices[selected_device_index]
            if int(selected_device["max_input_channels"]) < 1:
                QMessageBox.warning(
                    self,
                    "마이크 설정 실패",
                    "선택한 장치는 마이크 입력을 지원하지 않습니다.",
                )
                return

        self.settings.setValue(
            "audio/input_device",
            selected_device_index
            if selected_device_index is not None
            else -1,
        )
        self.settings.setValue(
            "agent/max_tool_rounds",
            dialog.max_tool_rounds,
        )
        self.settings.setValue(
            "agent/tool_calling_mode",
            dialog.tool_calling_mode,
        )
        self.settings.setValue(
            "desktop/control_session_seconds",
            dialog.desktop_control_seconds,
        )
        self.settings.setValue(
            "desktop/enabled",
            dialog.desktop_control_enabled,
        )
        self.settings.setValue(
            "speech/recognition_engine",
            dialog.recognition_engine,
        )
        self.settings.setValue(
            "speech/whisper_model",
            dialog.whisper_model,
        )
        self.settings.setValue(
            "speech/nim_api_key",
            dialog.nvidia_api_key,
        )
        self.nim_api_key = dialog.nvidia_api_key
        self.settings.setValue(
            "speech/fix_transcript",
            dialog.transcript_correction_enabled,
        )
        self.voice_transcript_correction_enabled = (
            dialog.transcript_correction_enabled
        )
        if selected_device is None:
            self.settings.remove("audio/input_device_name")
            self.settings.remove("audio/input_device_host_api")
            self.settings.remove("audio/input_device_channels")
        else:
            self.settings.setValue(
                "audio/input_device_name",
                selected_device["name"],
            )
            self.settings.setValue(
                "audio/input_device_host_api",
                int(selected_device["hostapi"]),
            )
            self.settings.setValue(
                "audio/input_device_channels",
                int(selected_device["max_input_channels"]),
            )
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(
                self,
                "설정 저장 실패",
                "설정을 저장하지 못했습니다. 다시 시도해 주세요.",
            )
            return
        self.microphone_device_index = selected_device_index
        self.max_tool_rounds = dialog.max_tool_rounds
        self.tool_calling_mode = dialog.tool_calling_mode
        previous_desktop_enabled = self.desktop_control_enabled
        self.desktop_control_enabled = dialog.desktop_control_enabled
        self._sync_desktop_tools()
        if self.desktop_control_enabled != previous_desktop_enabled:
            self._update_system_prompt()
        self.desktop_control_seconds = dialog.desktop_control_seconds
        self.desktop_control_session.set_duration_seconds(
            self.desktop_control_seconds
        )
        existing_desktop_dialog = getattr(self, "desktop_control_dialog", None)
        if not self.desktop_control_enabled:
            self.desktop_control_session.stop()
            if existing_desktop_dialog is not None:
                existing_desktop_dialog.close()
        elif existing_desktop_dialog is not None:
            existing_desktop_dialog.update_session_duration()
        self.tool_rounds_error = None
        self.tool_calling_mode_error = None
        self.desktop_control_settings_error = None
        self.microphone_notice = None
        self._apply_voice_engine(
            dialog.recognition_engine,
            dialog.whisper_model,
            dialog.nvidia_api_key,
        )
        engine_label = VOICE_ENGINE_LABELS[dialog.recognition_engine]
        self.status_label.setText(
            f"입력 마이크: {dialog.selected_device_name} · {engine_label}"
        )

    def _apply_voice_engine(
        self,
        engine: str,
        whisper_model: str,
        nim_api_key: str | None = None,
    ) -> None:
        engine = normalize_voice_engine(engine)
        if whisper_model not in WHISPER_MODEL_CHOICES:
            whisper_model = WHISPER_DEFAULT_MODEL
        if nim_api_key is not None:
            self.nim_api_key = nim_api_key.strip()
        if (
            engine == self.voice_engine
            and whisper_model == self.whisper_model
            and nim_api_key is None
        ):
            return
        self.voice_engine = engine
        self.whisper_model = whisper_model
        previous_recognizer = self.voice_recognizer
        self.voice_recognizer = create_voice_recognizer(
            engine,
            whisper_model,
            self.nim_api_key,
        )
        threading.Thread(
            target=previous_recognizer.close,
            name="quasar-asr-cleanup",
            daemon=True,
        ).start()

    def _load_model_options(self) -> None:
        if self.client is None or self.busy:
            return
        self.refresh_models_button.setEnabled(False)
        if self.model_refresh_radial_button is not None:
            self.model_refresh_radial_button.setEnabled(False)
        self.status_label.setText(f"{self.provider.upper()} 모델 목록을 불러오는 중...")
        loader = ModelLoader(
            self.provider,
            self.client,
            self.signals.models_loaded.emit,
        )
        self.model_loader = loader
        loader.start()

    def _apply_model_options(
        self,
        provider: str,
        model_names: set[str],
        errors: list[str],
    ) -> None:
        if self.closing:
            return
        if provider != self.provider:
            return
        if self.model:
            model_names.add(self.model)
        current_model = self.model_selector.currentText().strip() or self.model
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        self.model_selector.addItems(sorted(model_names))
        self.model_selector.setEditText(current_model)
        self.model_selector.blockSignals(False)
        self.refresh_models_button.setEnabled(True)
        if self.model_refresh_radial_button is not None:
            self.model_refresh_radial_button.setEnabled(True)
        if errors:
            status = (
                f"모델 목록을 모두 불러오지 못했습니다. 이름을 직접 입력할 수 있습니다. "
                f"({errors[0]})"
            )
            if self.microphone_notice:
                status = f"{status} · {self.microphone_notice}"
            self.status_label.setText(status)
        else:
            self._refresh_status()

    def _refresh_status(self) -> None:
        if self.startup_error:
            status = (
                f"AI 연결 설정을 확인해 주세요: {self.startup_error}"
            )
        elif self.client is not None:
            status = f"{self.provider.upper()} · {self.model or '모델을 선택하세요'}"
        else:
            status = ""
        if self.microphone_notice:
            status = f"{status} · {self.microphone_notice}" if status else self.microphone_notice
        if self.mcp_startup_error:
            status = f"{status} · {self.mcp_startup_error}" if status else self.mcp_startup_error
        if self.tool_rounds_error:
            tool_rounds_label = (
                "무제한"
                if self.max_tool_rounds == agent.UNLIMITED_TOOL_ROUNDS
                else str(self.max_tool_rounds)
            )
            status_message = (
                f"도구 반복 한도 설정 오류: {self.tool_rounds_error} "
                f"(기본값 {tool_rounds_label} 사용)"
            )
            status = f"{status} · {status_message}" if status else status_message
        if self.tool_calling_mode_error:
            status_message = f"도구 호출 방식 설정 오류: {self.tool_calling_mode_error}"
            status = f"{status} · {status_message}" if status else status_message
        if self.desktop_control_settings_error:
            status_message = (
                "화면 제어 시간 설정 오류: "
                f"{self.desktop_control_settings_error} "
                f"(기본값 {DEFAULT_SESSION_SECONDS}초 사용)"
            )
            status = f"{status} · {status_message}" if status else status_message
        if self.chat_history_error:
            status_message = f"채팅 기록 저장 오류: {self.chat_history_error}"
            status = f"{status} · {status_message}" if status else status_message
        if self.live_analysis_server_error:
            status_message = f"VS Code LSP 서버 오류: {self.live_analysis_server_error}"
            status = f"{status} · {status_message}" if status else status_message
        if status:
            self.status_label.setText(status)

    def _show_lsp_connection(self) -> None:
        if self.live_analysis_server is None:
            QMessageBox.warning(
                self,
                "VS Code LSP 연결",
                f"로컬 LSP 서버를 사용할 수 없습니다: "
                f"{self.live_analysis_server_error or '알 수 없는 오류'}",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("VS Code LSP 연결")
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "VS Code에서 'Quasar: Connect Live Analysis'를 실행한 뒤 "
                "아래 Endpoint와 Token을 입력하세요."
            )
        )
        form = QFormLayout()
        endpoint = QLineEdit(self.live_analysis_server.endpoint)
        endpoint.setReadOnly(True)
        token = QLineEdit(self.live_analysis_server.token)
        token.setReadOnly(True)
        form.addRow("Endpoint", endpoint)
        form.addRow("Token", token)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(560, 150)
        dialog.exec()

    def _ensure_office_monitor(self) -> agent.CliCollaborationMonitor:
        if self.office_monitor is not None:
            return self.office_monitor
        model = self.model_selector.currentText().strip() or self.model
        if not model:
            raise ValueError("Office 감시를 시작하기 전에 AI 모델을 선택해 주세요.")
        client, selected_model = agent.create_client(self.provider, model)
        workspace = Path(__file__).resolve().parent.parent
        source = _OfficeDocumentSource(workspace)
        settings = agent.load_collaboration_settings(
            workspace / "config" / "collaboration_config.json"
        )
        try:
            monitor = agent.CliCollaborationMonitor(
                client,
                selected_model,
                source,
                settings,
                allow_external_office_files=True,
            )
        except Exception:
            client.close()
            raise
        self.office_monitor_client = client
        self.office_monitor_source = source
        self.office_monitor = monitor
        return monitor

    def _office_monitor_for_chat(
        self,
    ) -> agent.CliCollaborationMonitor | None:
        if self.office_monitor is not None:
            return self.office_monitor
        workspace = Path(__file__).resolve().parent.parent
        watchlist_path = workspace / ".quasar" / "collaboration_office.json"
        if not watchlist_path.is_file():
            return None
        try:
            watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"연결된 Office 파일 목록을 읽지 못했습니다: {error}") from error
        if not isinstance(watchlist, list) or not all(
            isinstance(path, str) for path in watchlist
        ):
            raise ValueError("연결된 Office 파일 목록 형식이 올바르지 않습니다.")
        if not watchlist:
            return None
        return self._ensure_office_monitor()

    def _dispose_office_monitor(self) -> None:
        monitor = self.office_monitor
        client = self.office_monitor_client
        self.office_monitor = None
        self.office_monitor_client = None
        self.office_monitor_source = None
        if monitor is not None:
            try:
                monitor.set_enabled(False)
            except Exception as error:
                print(
                    f"Office 감시 종료 실패: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
            threading.Thread(
                target=self._close_office_monitor_resources,
                args=(monitor, client),
                name="quasar-office-monitor-cleanup",
                daemon=True,
            ).start()
            return
        if client is not None:
            threading.Thread(
                target=self._close_office_client,
                args=(client,),
                name="quasar-office-client-cleanup",
                daemon=True,
            ).start()

    @staticmethod
    def _close_office_monitor_resources(
        monitor: agent.CliCollaborationMonitor,
        client: Any | None,
    ) -> None:
        try:
            monitor_stopped = monitor.close()
        except Exception as error:
            monitor_stopped = False
            print(
                f"Office 감시 종료 실패: {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )
        if not monitor_stopped:
            monitor.wait_until_closed()
        if client is not None:
            QuasarDesktopApp._close_office_client(client)

    @staticmethod
    def _close_office_client(client: Any) -> None:
        try:
            client.close()
        except Exception as error:
            print(
                f"Office 감시 연결 종료 실패: {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )

    def _reset_office_monitor(self) -> bool:
        monitor = self.office_monitor
        if monitor is None:
            return False
        was_enabled = monitor.is_enabled()
        self._dispose_office_monitor()
        return was_enabled

    def _open_office_watch_dialog(self) -> None:
        try:
            monitor = self._ensure_office_monitor()
        except Exception as error:
            QMessageBox.warning(
                self,
                "Office 감시 준비 실패",
                f"{type(error).__name__}: {error}",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("문서 공동작업 감시")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        description = QLabel(
            "등록한 Word/PPT/메모장 텍스트 파일은 저장 후 검토합니다. 문서 내용이 선택된 AI 제공자에 "
            "전송될 수 있고, 일반 채팅 질문에도 참고 문맥으로 포함됩니다. 문서는 수정 "
            "제안을 승인할 때만 변경됩니다. 텍스트 편집기에 저장하지 않은 내용은 감지하지 "
            "못하므로 수정 제안을 적용하기 전에 저장해 주세요."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        file_list = QListWidget()
        file_list.addItems(monitor.list_office_documents())
        layout.addWidget(file_list)

        controls = QHBoxLayout()
        add_button = QPushButton("파일 추가")
        remove_button = QPushButton("선택 제거")
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        enabled_checkbox = QCheckBox("공동작업 감시 켜기")
        enabled_checkbox.setChecked(monitor.is_enabled())
        layout.addWidget(enabled_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(dialog.reject)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)

        def add_office_file() -> None:
            file_path, _selected_filter = QFileDialog.getOpenFileName(
                dialog,
                "감시할 Word, PowerPoint 또는 메모장 텍스트 파일 선택",
                str(Path.home()),
                "공동작업 문서 (*.docx *.pptx *.txt)",
            )
            if not file_path:
                return
            result = monitor.add_office_document(file_path)
            if "추가했습니다:" not in result:
                QMessageBox.warning(dialog, "파일 등록 실패", result)
            file_list.clear()
            file_list.addItems(monitor.list_office_documents())

        def remove_office_file() -> None:
            selected = file_list.currentItem()
            if selected is None:
                QMessageBox.information(dialog, "파일 선택", "목록에서 파일을 선택해 주세요.")
                return
            result = monitor.remove_office_document(selected.text())
            if "제거했습니다:" not in result:
                QMessageBox.warning(dialog, "파일 제거 실패", result)
            file_list.clear()
            file_list.addItems(monitor.list_office_documents())

        add_button.clicked.connect(add_office_file)
        remove_button.clicked.connect(remove_office_file)
        enabled_checkbox.toggled.connect(
            lambda enabled: self._set_office_monitor_enabled(
                enabled,
                enabled_checkbox,
            )
        )
        dialog.exec()

    def _set_office_monitor_enabled(
        self,
        enabled: bool,
        checkbox: QCheckBox | None = None,
    ) -> None:
        if self.closing:
            return
        if enabled:
            try:
                monitor = self._ensure_office_monitor()
            except Exception as error:
                QMessageBox.warning(
                    self,
                    "Office 감시 시작 실패",
                    f"{type(error).__name__}: {error}",
                )
                if checkbox is not None:
                    was_blocked = checkbox.blockSignals(True)
                    try:
                        checkbox.setChecked(False)
                    finally:
                        checkbox.blockSignals(was_blocked)
                return
            monitor.set_enabled(True)
            self.status_label.setText(
                f"문서 감시 중 · {len(monitor.list_office_documents())}개 파일"
            )
            return
        if self.office_monitor is not None:
            self.office_monitor.set_enabled(False)
            self.status_label.setText("문서 감시를 껐습니다.")

    def _poll_office_monitor(self) -> None:
        monitor = self.office_monitor
        if self.closing or monitor is None:
            return
        for question in monitor.take_pending_questions():
            self._add_message("assistant", question)
            self._queue_collaboration_speech(question)
        for proposal_id, proposal in monitor.take_pending_office_proposals():
            self._show_office_proposal(proposal_id, proposal)

    def _show_office_proposal(
        self,
        proposal_id: int,
        proposal: dict[str, str],
    ) -> None:
        monitor = self.office_monitor
        if monitor is None:
            return
        content = (
            f"**문서 수정 제안 {proposal_id}** · `{Path(proposal['path']).name}`\n\n"
            f"이유: {proposal['reason']}\n\n"
            f"기존 문단:\n> {proposal['before']}\n\n"
            f"수정 제안:\n> {proposal['replacement']}"
        )
        bubble, _body = self._add_message("assistant", content)
        spoken_proposal = (
            f"{Path(proposal['path']).name}에 수정 제안이 있습니다. "
            f"이유: {proposal['reason']}. "
            f"제안 내용: {proposal['replacement']}. "
            "채팅에서 승인하거나 거절할 수 있습니다."
        )
        self._queue_collaboration_speech(spoken_proposal)
        buttons = QHBoxLayout()
        approve_button = QPushButton("승인하고 적용")
        reject_button = QPushButton("거절")
        buttons.addWidget(approve_button)
        buttons.addWidget(reject_button)
        buttons.addStretch(1)
        bubble.layout().addLayout(buttons)

        def finish_proposal(approved: bool) -> None:
            active_monitor = self.office_monitor
            if active_monitor is None:
                return
            result = (
                active_monitor.approve_office_proposal(proposal_id)
                if approved
                else active_monitor.reject_office_proposal(proposal_id)
            )
            approve_button.setEnabled(False)
            reject_button.setEnabled(False)
            self._add_message("assistant", result)
            self._queue_collaboration_speech(
                f"문서 수정 제안을 {'적용했습니다' if approved else '거절했습니다'}."
            )

        approve_button.clicked.connect(lambda: finish_proposal(True))
        reject_button.clicked.connect(lambda: finish_proposal(False))

    def _clear_chat_view(self) -> None:
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
                child_layout.deleteLater()
            elif item.widget() is not None:
                item.widget().deleteLater()

    def _save_chat_history(
        self,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        if self.temporary_chat or self.chat_history_store is None:
            return
        messages_to_save = messages if messages is not None else self.messages
        if not any(
            message.get("role") == "user"
            and isinstance(message.get("content"), str)
            for message in messages_to_save
        ):
            return
        try:
            self.current_chat_id = self.chat_history_store.save(
                self.current_chat_id,
                messages_to_save,
            )
            self.chat_history_error = None
        except (OSError, sqlite3.Error, ValueError) as error:
            self.chat_history_error = str(error)
            self.status_label.setText(f"채팅 기록 저장 오류: {error}")

    def _update_chat_mode_ui(self) -> None:
        self.control_core_title.setText(
            "임시 채팅" if self.temporary_chat else "QUASAR"
        )
        if self.temporary_chat_button is not None:
            self.temporary_chat_button.setText(
                "임시 종료" if self.temporary_chat else "임시 채팅"
            )

    def _toggle_radial_menu(self) -> None:
        if self.control_overlay.isVisible():
            self._close_radial_menu()
            return
        self._layout_radial_menu()
        self.control_overlay.show()
        self.control_overlay.raise_()
        self.menu_button.setText("×")
        self.menu_button.setAccessibleName("Quasar 메뉴 닫기")
        self.control_overlay.setFocus()
        self._start_radial_animation(True)

    def _close_radial_menu(self) -> None:
        if (
            not hasattr(self, "control_overlay")
            or not self.control_overlay.isVisible()
        ):
            return
        self._start_radial_animation(False)

    def _start_radial_animation(self, opening: bool) -> None:
        self.radial_animation_timer.stop()
        self.radial_animation_from_progress = self.radial_animation_progress
        self.radial_animation_to_progress = 1.0 if opening else 0.0
        self.radial_animation_opening = opening
        self.radial_animation_started_at = time.perf_counter()
        distance = abs(
            self.radial_animation_to_progress
            - self.radial_animation_from_progress
        )
        self.radial_animation_duration_ms = max(120, round(480 * distance))
        for button in self.radial_buttons:
            button.setEnabled(False)
        if distance == 0:
            self._finish_radial_animation()
            return
        self.radial_animation_timer.start()

    def _advance_radial_animation(self) -> None:
        elapsed_ms = (time.perf_counter() - self.radial_animation_started_at) * 1000
        progress = min(1.0, elapsed_ms / self.radial_animation_duration_ms)
        eased = progress * progress * (3 - 2 * progress)
        self.radial_animation_progress = (
            self.radial_animation_from_progress
            + (
                self.radial_animation_to_progress
                - self.radial_animation_from_progress
            )
            * eased
        )
        self._layout_radial_menu()
        if progress >= 1.0:
            self._finish_radial_animation()

    def _finish_radial_animation(self) -> None:
        self.radial_animation_timer.stop()
        self.radial_animation_progress = self.radial_animation_to_progress
        self._layout_radial_menu()
        if self.radial_animation_opening:
            for button in self.radial_buttons:
                button.setEnabled(True)
        else:
            self.control_overlay.hide()
            self.menu_button.setText("☰")
            self.menu_button.setAccessibleName("Quasar 메뉴 열기")

    def _open_task_window(self) -> None:
        """현재 창과 독립적으로 동작하는 새 작업 창을 연다."""
        if self.closing:
            return
        window = TaskWindow(self)
        self.task_windows.append(window)
        window.destroyed.connect(
            lambda *_args, w=window: self._forget_task_window(w)
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def _forget_task_window(self, window: "TaskWindow") -> None:
        if window in self.task_windows:
            self.task_windows.remove(window)

    def _start_new_chat(self) -> None:
        if self.busy:
            QMessageBox.information(
                self,
                "새 채팅",
                "응답이 끝난 뒤 새 채팅을 시작해 주세요.",
            )
            return
        if self.temporary_chat:
            QMessageBox.information(
                self,
                "새 채팅",
                "임시 채팅을 종료한 뒤 새 채팅을 시작해 주세요.",
            )
            return
        self._save_chat_history()
        self.current_chat_id = None
        self.messages = [{"role": "system", "content": build_system_prompt(getattr(self, "desktop_control_enabled", False))}]
        self._clear_chat_view()
        self.status_label.setText("새 채팅을 시작했습니다.")

    def _toggle_temporary_chat(self) -> None:
        if self.busy:
            QMessageBox.information(
                self,
                "임시 채팅",
                "응답이 끝난 뒤 임시 채팅을 전환해 주세요.",
            )
            return
        if not self.temporary_chat:
            self._save_chat_history()
            self.suspended_chat = (self.current_chat_id, list(self.messages))
            self.current_chat_id = None
            self.messages = [{"role": "system", "content": build_system_prompt(getattr(self, "desktop_control_enabled", False))}]
            self.temporary_chat = True
            self._clear_chat_view()
            self._update_chat_mode_ui()
            self.status_label.setText(
                "임시 채팅입니다. 이 대화는 저장되지 않습니다."
            )
            return

        self.temporary_chat = False
        if self.suspended_chat is None:
            self.current_chat_id = None
            self.messages = [{"role": "system", "content": build_system_prompt(getattr(self, "desktop_control_enabled", False))}]
        else:
            self.current_chat_id, self.messages = self.suspended_chat
        self.suspended_chat = None
        self._clear_chat_view()
        for message in self.messages:
            if message.get("role") in {"user", "assistant"}:
                self._add_message(message["role"], message["content"])
        self._update_chat_mode_ui()
        self.status_label.setText("이전 채팅으로 돌아왔습니다.")

    def _open_recent_chats(self) -> None:
        if self.busy:
            QMessageBox.information(
                self,
                "최근 채팅",
                "응답이 끝난 뒤 최근 채팅을 열어 주세요.",
            )
            return
        if self.chat_history_store is None:
            QMessageBox.warning(
                self,
                "최근 채팅",
                f"채팅 기록 저장소를 사용할 수 없습니다: "
                f"{self.chat_history_error or '알 수 없는 오류'}",
            )
            return
        history_store = self.chat_history_store
        if self.temporary_chat:
            answer = QMessageBox.question(
                self,
                "임시 채팅 종료",
                "최근 채팅을 열면 현재 임시 대화는 저장하지 않고 종료됩니다. 계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._toggle_temporary_chat()

        dialog = QDialog(self)
        dialog.setWindowTitle("채팅 목록")
        layout = QVBoxLayout(dialog)
        count_label = QLabel()
        chat_list = QListWidget()
        chat_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(chat_list)
        layout.insertWidget(0, count_label)
        buttons = QHBoxLayout()
        open_button = QPushButton("열기")
        delete_button = QPushButton("선택 삭제")
        delete_all_button = QPushButton("전체 삭제")
        close_button = QPushButton("닫기")
        open_button.setEnabled(False)
        delete_button.setEnabled(False)
        delete_all_button.setEnabled(False)
        buttons.addWidget(open_button)
        buttons.addWidget(delete_button)
        buttons.addWidget(delete_all_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        close_button.clicked.connect(dialog.reject)
        chat_list.itemDoubleClicked.connect(lambda _item: dialog.accept())

        def refresh_chat_list() -> None:
            try:
                conversations = history_store.list_recent()
            except (OSError, sqlite3.Error, ValueError) as error:
                self.chat_history_error = str(error)
                chat_list.clear()
                count_label.setText("채팅 목록을 불러오지 못했습니다.")
                open_button.setEnabled(False)
                delete_button.setEnabled(False)
                delete_all_button.setEnabled(False)
                QMessageBox.warning(
                    dialog,
                    "채팅 목록",
                    f"채팅 목록을 불러오지 못했습니다: {error}",
                )
                return
            chat_list.clear()
            for conversation in conversations:
                updated_at = conversation["updated_at"][:16].replace("T", " ")
                item = QListWidgetItem(
                    f"{conversation['title']}\n{updated_at}"
                )
                item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
                chat_list.addItem(item)
            count_label.setText(
                f"저장된 채팅 {len(conversations)}개"
                if conversations
                else "저장된 채팅이 없습니다."
            )
            open_button.setEnabled(bool(conversations))
            delete_button.setEnabled(bool(conversations))
            delete_all_button.setEnabled(bool(conversations))

        def clear_current_chat(chat_id: str | None = None) -> None:
            if chat_id is not None and self.current_chat_id != chat_id:
                if (
                    self.suspended_chat is not None
                    and self.suspended_chat[0] == chat_id
                ):
                    self.suspended_chat = None
                return
            self.current_chat_id = None
            self.suspended_chat = None
            if not self.temporary_chat:
                self.messages = [{"role": "system", "content": build_system_prompt(getattr(self, "desktop_control_enabled", False))}]
                self._clear_chat_view()

        def delete_selected_chat() -> None:
            selected_item = chat_list.currentItem()
            if selected_item is None:
                return
            chat_id = selected_item.data(Qt.ItemDataRole.UserRole)
            answer = QMessageBox.question(
                dialog,
                "채팅 삭제",
                "선택한 채팅을 삭제할까요? 이 작업은 되돌릴 수 없습니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                deleted = history_store.delete(chat_id)
            except (OSError, sqlite3.Error, ValueError) as error:
                self.chat_history_error = str(error)
                QMessageBox.warning(
                    dialog,
                    "채팅 삭제 실패",
                    f"선택한 채팅을 삭제하지 못했습니다: {error}",
                )
                return
            if deleted:
                self.chat_history_error = None
                clear_current_chat(chat_id)
                self.status_label.setText("선택한 채팅을 삭제했습니다.")
                refresh_chat_list()

        def delete_all_chats() -> None:
            answer = QMessageBox.question(
                dialog,
                "전체 채팅 삭제",
                "저장된 모든 채팅을 삭제할까요? 이 작업은 되돌릴 수 없습니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                deleted_count = history_store.delete_all()
            except (OSError, sqlite3.Error, ValueError) as error:
                self.chat_history_error = str(error)
                QMessageBox.warning(
                    dialog,
                    "채팅 삭제 실패",
                    f"채팅 목록을 삭제하지 못했습니다: {error}",
                )
                return
            self.chat_history_error = None
            clear_current_chat()
            self.status_label.setText(
                f"저장된 채팅 {deleted_count}개를 삭제했습니다."
            )
            refresh_chat_list()

        def open_selected_chat() -> None:
            if chat_list.currentItem() is not None:
                dialog.accept()

        open_button.clicked.connect(open_selected_chat)
        delete_button.clicked.connect(delete_selected_chat)
        delete_all_button.clicked.connect(delete_all_chats)
        refresh_chat_list()
        dialog.resize(440, 520)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_item = chat_list.currentItem()
        if selected_item is not None:
            self._load_recent_chat(selected_item.data(Qt.ItemDataRole.UserRole))

    def _load_recent_chat(self, chat_id: str) -> None:
        if self.chat_history_store is None:
            return
        self._save_chat_history()
        try:
            conversation = self.chat_history_store.get(chat_id)
        except (OSError, sqlite3.Error, ValueError) as error:
            self.chat_history_error = str(error)
            QMessageBox.warning(
                self,
                "최근 채팅",
                f"채팅을 불러오지 못했습니다: {error}",
            )
            return
        if conversation is None:
            QMessageBox.warning(
                self,
                "최근 채팅",
                "선택한 채팅 기록을 찾을 수 없습니다.",
            )
            return
        self.current_chat_id = chat_id
        self.messages = [
            {"role": "system", "content": build_system_prompt(getattr(self, "desktop_control_enabled", False))},
            *conversation["messages"],
        ]
        self._clear_chat_view()
        for message in conversation["messages"]:
            self._add_message(message["role"], message["content"])
        self.status_label.setText(f"채팅을 열었습니다: {conversation['title']}")

    def _run_chat_command(self, command: str) -> str:
        monitor = None
        command_name = command.strip().split(maxsplit=1)[0].lower()
        if command_name == "/collab":
            try:
                monitor = self._ensure_office_monitor()
            except Exception as error:
                return f"명령 실행 실패: {type(error).__name__}: {error}"

        output: list[str] = []
        try:
            agent.handle_cli_command(
                command,
                self.messages,
                collaboration_monitor=monitor,
                output=output.append,
            )
        except Exception as error:
            return f"명령 실행 실패: {type(error).__name__}: {error}"

        result = "\n".join(output).strip()
        if command.strip().lower() == "/clear":
            self._clear_chat_view()
            self.current_chat_id = None
        return result or "명령을 실행했습니다."

    def _configure_agent(self) -> None:
        agent.current_directory = Path(__file__).resolve().parent.parent
        agent.auto_approve_commands = True
        agent.run_tool = self._run_tool
        agent.tool_approval_callback = self._request_tool_approval
        try:
            self.live_analysis_server = agent.CliLiveAnalysisServer(
                agent.current_directory
            )
        except OSError as error:
            self.live_analysis_server_error = str(error)
        if hasattr(self, "signals"):
            agent.alarm_notification_callback = self.signals.alarm_triggered.emit
        mcp_config_path = agent.resolve_mcp_config_path(
            None,
            agent.current_directory,
            Path(__file__).resolve().parent.parent,
        )
        try:
            self.mcp_manager = agent.MCPServerManager(mcp_config_path)
        except (OSError, ValueError) as error:
            self.mcp_startup_error = (
                f"MCP 설정을 읽지 못했습니다 ({mcp_config_path}): {error}"
            )

    def _request_tool_approval(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> bool:
        event = threading.Event()
        request = {"event": event, "approved": False}
        details = json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
        if len(details) > 4000:
            details = details[:4000] + "\n... (나머지 생략)"
        _session_for_tool_context(self).signals.tool_approval_requested.emit(
            name, details, request
        )
        if not event.wait(timeout=120):
            return False
        return bool(request["approved"])

    def _handle_desktop_start_session(self) -> str:
        if self.desktop_control_session.is_active():
            return (
                "데스크톱 제어 세션이 이미 활성 상태입니다. "
                f"남은 시간 {self.desktop_control_session.remaining_seconds()}초."
            )
        approved = self._request_tool_approval(
            "desktop_start_session",
            {
                "안내": (
                    "활성 세션 동안 에이전트가 화면을 관찰하고 "
                    "마우스·키 입력을 수행할 수 있습니다."
                ),
                "제한": (
                    f"세션은 최대 {self.desktop_control_seconds}초 또는 "
                    f"{MAX_ACTIONS_PER_SESSION}회 입력 후 자동으로 끝납니다."
                ),
            },
        )
        if not approved:
            return (
                "사용자가 데스크톱 제어 세션 시작을 승인하지 않았습니다. "
                "데스크톱 조작 없이 가능한 다른 방법을 제안하거나 작업을 중단하세요."
            )
        try:
            self.desktop_control_session.start()
        except RuntimeError as error:
            return f"데스크톱 제어 세션 시작 실패: {error}"
        return (
            "데스크톱 제어 세션이 시작되었습니다. "
            f"남은 시간 {self.desktop_control_session.remaining_seconds()}초, "
            f"세션당 최대 {MAX_ACTIONS_PER_SESSION}회 입력. "
            "이제 desktop_capture로 화면을 확인한 뒤 작업을 진행하세요."
        )

    def _open_desktop_control(self) -> None:
        if not self.desktop_control_enabled:
            QMessageBox.information(
                self,
                "데스크톱 제어",
                "설정에서 '데스크톱 제어 도구 사용'을 켜야 AI가 화면을 보고 "
                "마우스·키보드를 조작할 수 있습니다.",
            )
            return
        existing_dialog = getattr(self, "desktop_control_dialog", None)
        if existing_dialog is not None and existing_dialog.isVisible():
            existing_dialog.raise_()
            existing_dialog.activateWindow()
            return
        dialog = DesktopControlDialog(self.desktop_control_session, self)
        self.desktop_control_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: setattr(self, "desktop_control_dialog", None)
        )
        dialog.show()

    def _show_tool_approval(
        self,
        name: str,
        details: str,
        request: dict[str, Any],
    ) -> None:
        event = request.get("event")
        if not isinstance(event, threading.Event):
            return
        if self.closing:
            event.set()
            return
        answer = QMessageBox.warning(
            self,
            "위험 작업 승인",
            f"도구 '{name}'이(가) 다음 작업을 요청했습니다.\n\n{details}\n\n실행할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        request["approved"] = answer == QMessageBox.StandardButton.Yes
        event.set()

    def _run_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        live_analysis_server: Any = None,
        mcp_manager: Any = None,
    ) -> Any:
        if name == "set_tts_enabled":
            enabled = arguments.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("set_tts_enabled requires a boolean 'enabled' value.")
            self.signals.tts_enabled_requested.emit(enabled)
            return f"Quasar spoken responses {'enabled' if enabled else 'disabled'}."
        if name == "message_to_user":
            message, kind = agent.validate_user_message(arguments)
            _session_for_tool_context(self).signals.agent_message.emit(message, kind)
            return "메시지를 사용자 채팅에 표시했습니다."
        if name.startswith("desktop_"):
            try:
                if name == "desktop_start_session":
                    return self._handle_desktop_start_session()
                if name == "desktop_capture":
                    return self.desktop_control_session.capture_for_agent()
                if name == "desktop_move_mouse":
                    return self.desktop_control_session.move_mouse(
                        arguments.get("x"),
                        arguments.get("y"),
                    )
                if name == "desktop_click":
                    return self.desktop_control_session.click(
                        arguments.get("x"),
                        arguments.get("y"),
                        arguments.get("button", "left"),
                        arguments.get("double", False),
                    )
                if name == "desktop_scroll":
                    return self.desktop_control_session.scroll(
                        arguments.get("direction"),
                        arguments.get("clicks", 3),
                        arguments.get("x"),
                        arguments.get("y"),
                    )
                if name == "desktop_drag":
                    return self.desktop_control_session.drag(
                        arguments.get("x1"),
                        arguments.get("y1"),
                        arguments.get("x2"),
                        arguments.get("y2"),
                    )
                if name == "desktop_type_text":
                    return self.desktop_control_session.type_text(
                        arguments.get("text")
                    )
                if name == "desktop_press_key":
                    return self.desktop_control_session.press_key(
                        arguments.get("key")
                    )
                if name == "desktop_press_keys":
                    return self.desktop_control_session.press_keys(
                        arguments.get("keys")
                    )
            except (OSError, RuntimeError, ValueError) as error:
                message = str(error)
                if "활성화되어 있지 않거나 만료" in message:
                    return (
                        "데스크톱 제어 세션이 꺼져 있거나 만료되었습니다. 이는 작업 불가가 "
                        "아니라 세션이 꺼져 있을 뿐입니다. desktop_start_session 도구로 "
                        "사용자 승인을 요청해 세션을 시작한 뒤 같은 동작을 다시 시도하세요."
                    )
                return f"데스크톱 제어 실패: {type(error).__name__}: {error}"
            return f"알 수 없는 데스크톱 제어 도구: {name}"
        if name == "execute_cmd":
            return self.original_run_tool(
                name,
                arguments,
                live_analysis_server,
                mcp_manager,
            )
        return self.original_run_tool(
            name,
            arguments,
            live_analysis_server,
            mcp_manager,
        )

    def submit(self) -> None:
        if self.speech_worker is not None:
            self._stop_speech()
        selected_model = self.model_selector.currentText().strip()
        if selected_model:
            self.model = selected_model
        prompt = self.composer.toPlainText().strip()
        if not prompt:
            return
        voice_audio_b64 = self.pending_voice_audio_b64
        self.pending_voice_audio_b64 = ""
        if prompt.startswith("/"):
            self.composer.clear()
            if self.busy:
                self._enqueue_prompt(prompt, "")
            else:
                self._begin_prompt(prompt, "")
            return
        if self.busy:
            self.composer.clear()
            self._enqueue_prompt(prompt, voice_audio_b64)
            return
        if self.client is None:
            self.pending_voice_audio_b64 = voice_audio_b64
            self._add_message(
                "assistant",
                "AI 연결을 준비하지 못했습니다. 공급자 설정과 API 설정을 확인해 주세요.\n"
                f"상세 정보: {self.startup_error or '알 수 없는 초기화 오류'}",
            )
            return
        if not self.model:
            self.pending_voice_audio_b64 = voice_audio_b64
            self._add_message("assistant", "사용할 모델을 선택하거나 모델 이름을 입력해 주세요.")
            return
        self.composer.clear()
        self._begin_prompt(prompt, voice_audio_b64)

    def _enqueue_prompt(self, prompt: str, voice_audio_b64: str = "") -> None:
        self.pending_prompts.append((prompt, voice_audio_b64))
        preview = prompt if len(prompt) <= 60 else prompt[:57] + "..."
        self._add_activity_message(
            f"다음 작업을 대기열에 추가했습니다 "
            f"({len(self.pending_prompts)}개): {preview}"
        )
        self.status_label.setText(
            f"대기 중인 작업 {len(self.pending_prompts)}개 · "
            "현재 작업이 끝나면 순서대로 실행합니다."
        )

    def _start_next_pending_prompt(self) -> bool:
        if self.closing or self.busy:
            return False
        while self.pending_prompts:
            prompt, voice_audio_b64 = self.pending_prompts.popleft()
            if self._begin_prompt(prompt, voice_audio_b64):
                return True
        return False

    def _begin_prompt(self, prompt: str, voice_audio_b64: str = "") -> bool:
        if self.closing:
            return False
        if prompt.startswith("/"):
            if prompt.strip().lower() == "/clear":
                self._save_chat_history()
            self._add_message("user", prompt)
            result = self._run_chat_command(prompt)
            self._add_message("assistant", result)
            return False
        if self.client is None:
            self._add_message(
                "assistant",
                "AI 연결을 준비하지 못했습니다. 공급자 설정과 API 설정을 확인해 주세요.\n"
                f"상세 정보: {self.startup_error or '알 수 없는 초기화 오류'}",
            )
            return False
        if not self.model:
            self._add_message("assistant", "사용할 모델을 선택하거나 모델 이름을 입력해 주세요.")
            return False
        office_monitor_error = ""
        try:
            office_monitor = self._office_monitor_for_chat()
        except Exception as error:
            office_monitor = None
            office_monitor_error = (
                f"연결된 문서를 채팅 문맥에 불러오지 못했습니다: "
                f"{type(error).__name__}: {error}"
            )
        try:
            request_client, request_model = agent.create_client(
                self.provider,
                self.model,
            )
        except Exception as error:
            self._add_message(
                "assistant",
                f"요청 연결을 준비하지 못했습니다: {type(error).__name__}: {error}",
            )
            return False

        source_point = self.composer.mapTo(
            self.centralWidget(), self.composer.rect().center()
        )
        orb_center = self.orb.mapTo(
            self.centralWidget(), self.orb.hole_center()
        )
        self.black_hole_overlay.start(
            prompt,
            (source_point.x(), source_point.y()),
            (orb_center.x(), orb_center.y()),
        )
        self.orb.start_submission()
        self.composer.clear()
        self._add_message("user", prompt)
        self._save_chat_history(
            [*self.messages, {"role": "user", "content": prompt}]
        )
        if office_monitor_error:
            self._add_message("assistant", office_monitor_error)
        self.last_activity_message = None
        self._add_activity_message("요청을 분석하고 있습니다...")
        self.status_label.setText("요청을 분석하고 있습니다...")
        request_messages = list(self.messages)
        cancel_event = threading.Event()
        self.request_cancel_event = cancel_event
        self.request_client = request_client
        self.request_cancel_pending = False
        self.busy = True
        self._set_busy(True)
        model = request_model
        worker = threading.Thread(
            target=self._request_response,
            args=(
                request_client,
                model,
                prompt,
                request_messages,
                cancel_event,
                office_monitor,
                self.mcp_manager,
                self.max_tool_rounds,
                self.tool_calling_mode,
                voice_audio_b64 or None,
            ),
            daemon=True,
        )
        self.worker_thread = worker
        worker.start()
        return True

    def _request_response(
        self,
        client: Any,
        model: str,
        prompt: str,
        messages: list[dict[str, Any]],
        cancel_event: threading.Event,
        office_monitor: agent.CliCollaborationMonitor | None = None,
        mcp_manager: agent.MCPServerManager | None = None,
        max_tool_rounds: int | None = None,
        tool_calling_mode: str | None = None,
        audio_b64: str | None = None,
    ) -> None:
        try:
            try:
                office_context = ""
                if office_monitor is not None:
                    (
                        office_context,
                        office_context_errors,
                        included_documents,
                    ) = office_monitor.office_chat_context()
                    if included_documents:
                        self.signals.request_progress.emit(
                            f"연결된 문서 {included_documents}개를 참고합니다."
                        )
                    for error in office_context_errors:
                        self.signals.request_progress.emit(
                            f"연결 문서를 읽지 못했습니다: {error}"
                        )
                styled_messages = _voice_style_messages(self, messages)
                response = agent.request_agent(
                    client,
                    model,
                    styled_messages,
                    prompt,
                    live_analysis_server=getattr(
                        self,
                        "live_analysis_server",
                        None,
                    ),
                    mcp_manager=mcp_manager,
                    progress_callback=self.signals.request_progress.emit,
                    cancel_event=cancel_event,
                    office_context=office_context,
                    max_tool_rounds=max_tool_rounds,
                    tool_calling_mode=tool_calling_mode,
                    audio_b64=audio_b64,
                )
            except InterruptedError:
                self.signals.request_cancelled.emit()
                return
            except Exception as error:
                if cancel_event.is_set():
                    self.signals.request_cancelled.emit()
                    return
                self.signals.request_failed.emit(
                    f"{type(error).__name__}: {error}"
                )
                return
            if cancel_event.is_set():
                self.signals.request_cancelled.emit()
                return
            # request_agent가 [사용자 질문, 답변]을 덧붙인 목록을 그대로
            # 내보내야 다음 요청에 이번 대화가 포함된다.
            self.signals.response_ready.emit(response, styled_messages)
        finally:
            try:
                client.close()
            except Exception as error:
                print(
                    f"요청 연결 종료 실패: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )

    def _handle_send_button(self) -> None:
        if self.speech_worker is not None:
            self._stop_speech()
        elif self.busy:
            self._cancel_request()
        else:
            self.submit()

    def _cancel_request(self) -> None:
        if (
            not self.busy
            or self.request_cancel_event is None
            or self.request_cancel_pending
        ):
            return
        self.request_cancel_pending = True
        self.request_cancel_event.set()
        self.send_button.setText("중지 중...")
        self.send_button.setEnabled(False)
        self.status_label.setText(
            "응답 중지를 요청했습니다. 실행 중인 도구는 완료될 수 있습니다."
        )
        self.status_label.setText("응답을 중지하고 있습니다...")
        if self.request_client is not None:
            try:
                self.request_client.close()
            except Exception as error:
                self.status_label.setText(
                    f"요청 연결 중지 실패: {type(error).__name__}: {error}"
                )

    def _clear_request_state(self) -> None:
        self.request_cancel_event = None
        self.request_client = None
        self.request_cancel_pending = False

    def _start_voice_recording(self) -> None:
        if self.busy or self.voice_recording or self.voice_processing:
            return
        self._start_voice_server()
        self.voice_recording = True
        self.voice_stop_event = threading.Event()
        self.voice_button.setText("듣는 중... 놓으면 전송")
        self.status_label.setText("마이크를 통해 듣고 있습니다...")
        worker = VoiceCaptureWorker(
            self.voice_stop_event,
            self.voice_recognizer,
            self.signals,
            self.microphone_device_index,
        )
        self.voice_worker = worker
        worker.start()

    def _stop_voice_recording(self) -> None:
        if not self.voice_recording:
            return
        self.voice_recording = False
        self.voice_processing = True
        self.voice_button.setEnabled(False)
        self.voice_button.setText("음성 인식 중...")
        if self.voice_stop_event is not None:
            self.voice_stop_event.set()

    def _show_voice_status(self, status: str) -> None:
        if self.closing:
            return
        self.status_label.setText(status)
        if self.voice_conversation_active:
            self.galaxy_transition.set_conversation_status(status)
            if status.startswith("듣는 중... 편하게 말씀하시면 자동으로 전송합니다."):
                self.voice_conversation_retry_attempt = 0
        if self.voice_processing:
            self.voice_button.setText("음성 인식 중...")
        elif self.voice_recording:
            self.voice_button.setText("듣는 중... 놓으면 전송")

    def _emit_voice_text_final(self, text: str, conversation: bool) -> None:
        if conversation:
            self.signals.conversation_voice_text_final.emit(text)
        else:
            self.signals.voice_text_final.emit(text)

    def _correct_voice_text(self, text: str) -> None:
        self._correct_transcript(text, False)

    def _correct_voice_conversation_text(self, text: str) -> None:
        self._correct_transcript(text, True)

    def _correct_transcript(self, text: str, conversation: bool) -> None:
        if self.closing:
            return
        if self.voice_engine == VOICE_ENGINE_OMNI:
            # omni 엔진은 전사 문장이 아니라 음성 첨부가 실제 요청이므로
            # 자리표시자 텍스트를 따로 교정하지 않는다.
            self._emit_voice_text_final(text, conversation)
            return
        correction_worker = self.transcript_correction_worker
        if (
            not self.voice_transcript_correction_enabled
            or self.client is None
            or (
                correction_worker is not None
                and correction_worker.is_alive()
            )
        ):
            self._emit_voice_text_final(text, conversation)
            return
        try:
            client, model = agent.create_client(self.provider, self.model)
        except Exception:
            self._emit_voice_text_final(text, conversation)
            return
        self.signals.voice_status.emit("음성 인식 문장을 교정하는 중...")
        worker = TranscriptCorrectionWorker(
            client,
            model,
            text,
            self.signals,
            conversation=conversation,
        )
        self.transcript_correction_worker = worker
        worker.start()

    def _set_pending_voice_audio(self, audio_b64: str) -> None:
        """omni 엔진이 음성 WAV(base64)를 요청 전달 전에 잠시 보관한다."""
        self.pending_voice_audio_b64 = audio_b64

    def _submit_voice_text(self, text: str) -> None:
        if self.closing:
            return
        self.voice_processing = False
        self.voice_button.setEnabled(not self.busy)
        self.voice_button.setText("누르고 말하기")
        self.composer.setPlainText(text)
        self.submit()

    def _set_voice_conversation_active(self, active: bool) -> None:
        if self.closing:
            return
        self.voice_conversation_active = active
        self.voice_interrupt_shortcut.setEnabled(active)
        self.composer_hint.setText(
            "Ctrl+Space: 답변 음성 중지 후 말하기"
            if active
            else "Space 길게 누르기: 말하기  ·  Enter: 전송  ·  "
            "작업 중 Enter: 다음 작업"
        )
        if not active:
            self.voice_conversation_retry_timer.stop()
            if self.voice_conversation_stop_event is not None:
                self.voice_conversation_stop_event.set()
            self.voice_conversation_listening = False
            return
        self.voice_conversation_retry_attempt = 0
        self._start_voice_server()
        self.galaxy_transition.set_conversation_status(
            "듣는 중... 편하게 말씀하시면 자동으로 전송합니다."
        )
        self._start_voice_conversation_capture()

    def _interrupt_voice_conversation(self) -> None:
        if self.closing or not self.voice_conversation_active:
            return
        if self.speech_worker is not None:
            self._stop_speech()
            return
        if self.busy:
            self._cancel_request()
            return
        if not self.voice_conversation_listening:
            self._start_voice_conversation_capture()

    def _start_voice_conversation_capture(self) -> None:
        if (
            not self.voice_conversation_active
            or self.voice_conversation_listening
            or self.busy
            or self.closing
        ):
            return
        self.voice_conversation_stop_event = threading.Event()
        self.voice_conversation_listening = True
        worker = VoiceConversationCaptureWorker(
            self.voice_conversation_stop_event,
            self.voice_recognizer,
            self.signals,
            self.microphone_device_index,
        )
        self.voice_conversation_worker = worker
        worker.start()

    def _retry_voice_conversation(self) -> None:
        if (
            not self.voice_conversation_active
            or self.closing
            or self.busy
        ):
            return
        self._start_voice_server()
        self._start_voice_conversation_capture()

    def _start_voice_server(self) -> None:
        threading.Thread(
            target=self._prepare_voice_server,
            name="quasar-asr-startup",
            daemon=True,
        ).start()

    def _prepare_voice_server(self) -> None:
        try:
            self.voice_recognizer.start_server()
        except (OSError, RuntimeError) as error:
            self.signals.voice_status.emit(
                f"음성 인식기 준비 실패: {type(error).__name__}: {error}"
            )

    def _submit_voice_conversation_text(self, text: str) -> None:
        if self.closing or not self.voice_conversation_active:
            return
        self.voice_conversation_listening = False
        self.composer.setPlainText(text)
        self.submit()
        if not self.busy:
            self._start_voice_conversation_capture()

    def _finish_voice_conversation_error(self, error: str) -> None:
        if self.closing or not self.voice_conversation_active:
            return
        self.voice_conversation_listening = False
        if self.voice_conversation_stop_event is not None:
            self.voice_conversation_stop_event.set()
        self.voice_conversation_retry_attempt += 1
        retry_delay_ms = min(
            1_000 * 2 ** (self.voice_conversation_retry_attempt - 1),
            10_000,
        )
        retry_delay_seconds = retry_delay_ms // 1_000
        status = (
            f"음성 입력 오류: {error} · "
            f"{retry_delay_seconds}초 후 다시 듣습니다."
        )
        self.galaxy_transition.set_conversation_status(
            status
        )
        self.status_label.setText(status)
        self.voice_conversation_retry_timer.start(retry_delay_ms)

    def _finish_voice_error(self, error: str) -> None:
        if self.closing:
            return
        self.voice_recording = False
        self.voice_processing = False
        self.voice_button.setEnabled(not self.busy)
        self.voice_button.setText("누르고 말하기")
        self.status_label.setText(f"음성 입력 실패: {error}")
        self.voice_button.setFocus()

    def _set_tts_enabled(self, enabled: bool) -> None:
        if self.voice_response_toggle.isChecked() != enabled:
            self.voice_response_toggle.setChecked(enabled)
        self.settings.setValue("voice/tts_enabled", enabled)
        self.settings.sync()
        if not enabled and self.speech_worker is not None:
            self._stop_speech()
        if not enabled:
            self.pending_speech_messages.clear()

    def _speak_response(self, response: str) -> bool:
        spoken_text = re.sub(r"```.*?```", "코드 블록", response, flags=re.DOTALL)
        # 마크다운 링크 [텍스트](주소)는 텍스트만 남기고, 남은 URL은 '링크'로 치환해
        # 주소를 소리 내어 읽는 일이 없게 한다.
        spoken_text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", spoken_text)
        spoken_text = re.sub(r"https?://\S+", " 링크 ", spoken_text)
        spoken_text = re.sub(r"[*_#`>]", "", spoken_text).strip()
        spoken_text = re.sub(r"\s{2,}", " ", spoken_text)
        if not spoken_text:
            return False
        self.status_label.setText("음성 출력을 시작하고 있습니다...")
        if self.voice_conversation_active:
            self.galaxy_transition.set_conversation_status(
                "음성 출력을 재생하고 있습니다..."
            )
        worker = LocalSpeechOutputWorker(spoken_text, self.signals)
        self.speech_worker = worker
        self.send_button.setText("음성 중지")
        self.send_button.setEnabled(True)
        self.status_label.setText("음성을 재생 중입니다. 버튼을 눌러 중지할 수 있습니다.")
        worker.start()
        return True

    def _queue_collaboration_speech(self, message: str) -> None:
        if (
            self.closing
            or not (
                self.voice_response_toggle.isChecked()
                or bool(getattr(self, "voice_conversation_active", False))
            )
            or not message.strip()
        ):
            return
        self.pending_speech_messages.append(message.strip())
        self._start_next_pending_speech()

    def _start_next_pending_speech(self) -> bool:
        if self.closing or not (
            self.voice_response_toggle.isChecked()
            or bool(getattr(self, "voice_conversation_active", False))
        ):
            self.pending_speech_messages.clear()
            return False
        if self.speech_worker is not None:
            return False
        while self.pending_speech_messages:
            message = self.pending_speech_messages.popleft()
            if self._speak_response(message):
                return True
        return False

    def _stop_speech(self) -> None:
        worker = self.speech_worker
        if worker is None:
            self.pending_speech_messages.clear()
            return
        self.speech_worker = None
        self.pending_speech_messages.clear()
        worker.stop()
        self.send_button.setText("중지" if self.busy else "전송")
        if not self.busy:
            self.send_button.setEnabled(True)
        self.status_label.setText("음성 출력을 중지했습니다.")
        if self.voice_conversation_active:
            self.galaxy_transition.set_conversation_status(
                "음성을 멈췄습니다. 말씀해 주세요."
            )
            self._start_voice_conversation_capture()

    def _finish_speech(self, worker: LocalSpeechOutputWorker) -> None:
        if self.closing or worker is not self.speech_worker:
            return
        self.speech_worker = None
        self.send_button.setText("중지" if self.busy else "전송")
        self.send_button.setEnabled(True)
        if self._start_next_pending_speech():
            return
        if self._start_next_pending_prompt():
            return
        if self.busy:
            # 요청이 아직 진행 중이면 대화 재개는 응답 완료 후에 한다.
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()
        else:
            self.status_label.setText("음성 출력을 마쳤습니다.")

    def _finish_speech_error(
        self,
        worker: LocalSpeechOutputWorker,
        error: str,
    ) -> None:
        if self.closing or worker is not self.speech_worker:
            return
        self.speech_worker = None
        self.send_button.setText("전송")
        self.send_button.setEnabled(True)
        if self._start_next_pending_speech():
            return
        if self._start_next_pending_prompt():
            return
        if not self.closing:
            self.status_label.setText(f"음성 출력 실패: {error}")
            if self.voice_conversation_active:
                self.galaxy_transition.set_conversation_status(
                    f"음성 출력 실패: {error} 다시 말씀해 주세요."
                )
                self._start_voice_conversation_capture()

    def _show_request_progress(self, progress: str) -> None:
        if self.closing or self.request_cancel_pending:
            return
        self._add_activity_message(progress)
        self.status_label.setText(progress)

    def _show_agent_message(self, message: str, kind: str) -> None:
        if self.closing or self.request_cancel_pending:
            return
        self.last_activity_message = None
        content = (
            f"**오류 및 복구**\n\n{message}"
            if kind == "error"
            else message
        )
        self._add_message("assistant", content)
        self._queue_collaboration_speech(message)

    def _show_alarm_notification(self, message: str) -> None:
        if self.closing:
            return
        self._add_message("assistant", f"🔔 알람: {message}")
        self.status_label.setText(f"알람: {message}")

        notification = QMessageBox(self)
        notification.setIcon(QMessageBox.Icon.Information)
        notification.setWindowTitle("Quasar 알람")
        notification.setText(message)
        notification.setStandardButtons(QMessageBox.StandardButton.Ok)
        notification.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        notification.setWindowModality(Qt.WindowModality.NonModal)
        notification.finished.connect(
            lambda _result, dialog=notification: self._dismiss_alarm_notification(
                dialog
            )
        )
        self.alarm_notifications.append(notification)
        notification.show()

    def _dismiss_alarm_notification(self, notification: QMessageBox) -> None:
        if notification in self.alarm_notifications:
            self.alarm_notifications.remove(notification)
        notification.deleteLater()

    def _finish_with_response(
        self,
        response: str,
        request_messages: list[dict[str, Any]],
    ) -> None:
        if self.closing:
            return
        self.messages[:] = _strip_voice_style_messages(request_messages)
        self._save_chat_history()
        self._clear_request_state()
        self._add_message("assistant", response)
        self._set_busy(False)
        if self.voice_conversation_active:
            # 답변이 채팅에 전송되는 순간 우주 효과음을 울린다.
            # TTS 음성은 이후 합성이 끝나는 대로 따로 재생된다.
            play_cosmic_chime_async()
        if self.voice_response_toggle.isChecked():
            if self._speak_response(response):
                return
        if self._start_next_pending_speech():
            return
        if self._start_next_pending_prompt():
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()

    def _finish_with_error(self, error: str) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self.pending_speech_messages.clear()
        self._add_message("assistant", f"요청 처리 실패: {error}")
        self._set_busy(False)
        if self._start_next_pending_speech():
            return
        if self._start_next_pending_prompt():
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()

    def _finish_cancelled_request(self) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self.pending_speech_messages.clear()
        self.pending_prompts.clear()
        self.status_label.setText("응답 생성을 중지했습니다. 대기 중인 작업도 취소했습니다.")
        self._set_busy(False)
        if self._start_next_pending_speech():
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.setText("중지" if busy else "전송")
        self.send_button.setEnabled(True)
        self.provider_selector.setEnabled(not busy)
        self.model_selector.setEnabled(not busy)
        self.refresh_models_button.setEnabled(not busy)
        if self.model_refresh_radial_button is not None:
            self.model_refresh_radial_button.setEnabled(not busy)
        self.voice_button.setEnabled(not busy and not self.voice_processing)
        self.progress.setVisible(busy)
        self.orb.set_active(busy)
        if busy:
            self.status_label.setText("응답을 생성하는 중...")
            if self.voice_conversation_active:
                self.galaxy_transition.set_conversation_status(
                    "응답을 생성하고 있습니다..."
                )
        elif self.voice_conversation_active:
            self.status_label.setText("은하 음성 대화 중...")
        else:
            self._refresh_status()
            self.voice_button.setFocus()

    def _add_message(
        self,
        role: str,
        content: str,
        streaming: bool = False,
    ) -> tuple[QFrame, QLabel]:
        if self.empty_state is not None:
            self.chat_layout.removeWidget(self.empty_state)
            self.empty_state.deleteLater()
            self.empty_state = None

        bubble = QFrame()
        bubble.setObjectName("messageBubble")
        bubble.setStyleSheet(
            "#messageBubble {"
            f"background: {'#12192a' if role == 'assistant' else '#1a1b36'};"
            f"border: 1px solid {'#29314a' if role == 'assistant' else '#34345d'};"
            "border-radius: 16px;"
            "}"
        )
        bubble.setMaximumWidth(760)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(18, 14, 18, 14)
        bubble_layout.setSpacing(7)

        role_label = QLabel("QUASAR" if role == "assistant" else "YOU")
        role_label.setStyleSheet(
            f"color: {ACCENT if role == 'assistant' else '#aaa3e8'};"
            "font-size: 10px; font-weight: 800; letter-spacing: 0.5px;"
        )
        body = QLabel()
        body.setTextFormat(
            Qt.TextFormat.PlainText if streaming else Qt.TextFormat.MarkdownText
        )
        body.setText(content)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size: 13px; line-height: 1.45;")
        bubble_layout.addWidget(role_label)
        bubble_layout.addWidget(body)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if role == "assistant":
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            row.addStretch(1)
            row.addWidget(bubble)
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)
        return bubble, body

    def _add_activity_message(self, content: str) -> None:
        if self.last_activity_message == content:
            return
        self.last_activity_message = content

        message = QLabel(content)
        message.setObjectName("activityMessage")
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; padding: 2px 6px;"
        )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(message)
        row.addStretch(1)
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self, *_range: int) -> None:
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_checklist_visible(self, visible: bool) -> None:
        self.checklist_panel.setVisible(visible)
        self.checklist_button.setText(
            "체크리스트 닫기" if visible else "체크리스트 열기"
        )
        if visible:
            self._refresh_checklist()

    def _refresh_checklist(self) -> None:
        was_blocked = self.checklist_list.blockSignals(True)
        try:
            tasks = agent.todo_store.list_tasks("all")
            self.checklist_list.clear()
            for task in tasks:
                item = QListWidgetItem(task["title"])
                item.setData(Qt.ItemDataRole.UserRole, task["id"])
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    Qt.CheckState.Checked
                    if task["completed"]
                    else Qt.CheckState.Unchecked
                )
                self.checklist_list.addItem(item)
            self.checklist_status.setText(
                f"할 일 {len(tasks)}개"
                if tasks
                else "등록된 체크리스트가 없습니다."
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.checklist_list.clear()
            self.checklist_status.setText(
                f"체크리스트를 불러오지 못했습니다: {error}"
            )
        finally:
            self.checklist_list.blockSignals(was_blocked)

    def _set_checklist_item_completed(self, item: QListWidgetItem) -> None:
        task_id = item.data(Qt.ItemDataRole.UserRole)
        if task_id is None:
            return
        completed = item.checkState() == Qt.CheckState.Checked
        try:
            agent.todo_store.update_task(task_id, {"completed": completed})
        except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.checklist_status.setText(
                f"체크리스트 변경 실패: {error}"
            )
            self._refresh_checklist()
            return
        self._refresh_checklist()

    def closeEvent(self, event: Any) -> None:
        if self.busy:
            answer = QMessageBox.question(
                self,
                "Quasar 종료",
                "요청 처리를 중지하고 종료합니다. 이미 실행 중인 외부 작업은 "
                "완료될 수 있습니다.\n종료할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.closing = True
        for window in list(self.task_windows):
            window.close()
        self.task_windows.clear()
        self.pending_prompts.clear()
        self.office_monitor_timer.stop()
        self._dispose_office_monitor()
        if self.request_cancel_event is not None:
            self.request_cancel_event.set()
        if self.request_client is not None:
            try:
                self.request_client.close()
            except Exception as error:
                print(
                    f"요청 연결 종료 실패: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
        self.voice_conversation_active = False
        if self.voice_conversation_stop_event is not None:
            self.voice_conversation_stop_event.set()
        self.voice_conversation_retry_timer.stop()
        if self.voice_stop_event is not None:
            self.voice_stop_event.set()
        if self.speech_worker is not None:
            self.speech_worker.stop()
        self.voice_recognizer.close()
        self.desktop_control_session.stop()
        for schema in self.desktop_tool_schemas:
            if schema in agent.TOOLS:
                agent.TOOLS.remove(schema)
        if self.mcp_manager is not None:
            mcp_manager = self.mcp_manager
            self.mcp_manager = None
            if self.busy and self.worker_thread is not None:
                threading.Thread(
                    target=self._close_mcp_manager_after_worker,
                    args=(self.worker_thread, mcp_manager),
                    daemon=True,
                ).start()
            else:
                self._close_mcp_manager(mcp_manager)
        if self.live_analysis_server is not None:
            try:
                self.live_analysis_server.close()
            except (OSError, RuntimeError) as error:
                print(
                    f"VS Code LSP 서버 종료 실패: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
            self.live_analysis_server = None
        agent.run_tool = self.original_run_tool
        agent.tool_approval_callback = self.original_tool_approval_callback
        agent.alarm_notification_callback = (
            self.original_alarm_notification_callback
        )
        if self.tts_tool_schema in agent.TOOLS:
            agent.TOOLS.remove(self.tts_tool_schema)
        event.accept()

    @staticmethod
    def _close_mcp_manager_after_worker(
        worker: threading.Thread,
        mcp_manager: agent.MCPServerManager,
    ) -> None:
        worker.join()
        QuasarDesktopApp._close_mcp_manager(mcp_manager)

    @staticmethod
    def _close_mcp_manager(mcp_manager: agent.MCPServerManager) -> None:
        try:
            mcp_manager.close()
        except Exception as error:
            print(
                f"MCP 연결 종료 실패: {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )


class TaskWindow(QMainWindow):
    """메인 창과 독립적으로 요청을 실행하는 병렬 작업 창.

    공급자·모델 기본값·MCP·데스크톱 세션 같은 공용 자원은 ``app``을 통해
    공유하되, 대화 기록과 요청 상태, 대기열은 이 창이 따로 가진다. 따라서
    메인 창에서 작업이 돌아가는 동안에도 새 작업 창에서 다른 작업을 동시에
    진행할 수 있다(크롬 탭처럼). 도구 진행 메시지와 승인 요청은 요청을
    실행하는 스레드의 컨텍스트(app._tool_context)를 통해 이 창으로 라우팅된다.
    """

    def __init__(self, app: "QuasarDesktopApp", title: str = "새 작업") -> None:
        super().__init__()
        self.app = app
        self.closing = False
        self.title_text = title
        self.setWindowTitle(f"Quasar · {title}")
        self.setWindowIcon(app.windowIcon())
        self.resize(780, 640)
        self.setMinimumSize(560, 460)
        self.setStyleSheet(app.styleSheet())

        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_system_prompt(
                    getattr(app, "desktop_control_enabled", False)
                ),
            }
        ]
        self.busy = False
        self.worker_thread: threading.Thread | None = None
        self.request_cancel_event: threading.Event | None = None
        self.request_client: Any = None
        self.request_cancel_pending = False
        self.pending_prompts: deque[tuple[str, str]] = deque()
        self.last_activity_message: str | None = None
        self.model = app.model
        self.signals = AppSignals(self)
        self.signals.response_ready.connect(self._finish_with_response)
        self.signals.request_progress.connect(self._show_request_progress)
        self.signals.request_failed.connect(self._finish_with_error)
        self.signals.request_cancelled.connect(self._finish_cancelled_request)
        self.signals.agent_message.connect(self._show_agent_message)
        self.signals.tool_approval_requested.connect(self._show_tool_approval)
        self._build_ui()
        self._refresh_status()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("mainCanvas")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        header = QHBoxLayout(top_bar)
        header.setContentsMargins(12, 7, 10, 7)
        header.setSpacing(10)
        title = QLabel(f"🛰 {self.title_text}")
        title.setStyleSheet(f"color: {ACCENT}; font-weight: 800;")
        header.addWidget(title)
        header.addStretch(1)
        provider_label = QLabel(self.app.provider.upper())
        provider_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        provider_label.setToolTip("메인 창에서 선택한 공급자를 사용합니다.")
        header.addWidget(provider_label)
        self.model_selector = QComboBox()
        self.model_selector.setEditable(True)
        self.model_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_selector.setMinimumWidth(180)
        self.model_selector.setFixedHeight(36)
        for index in range(self.app.model_selector.count()):
            self.model_selector.addItem(self.app.model_selector.itemText(index))
        self.model_selector.setEditText(self.model)
        self.model_selector.setToolTip("이 작업 창에서 사용할 모델")
        header.addWidget(self.model_selector)
        layout.addWidget(top_bar)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; padding-left: 4px;"
        )
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        layout.addWidget(self.progress)

        conversation_panel = QFrame()
        conversation_panel.setObjectName("conversationPanel")
        conversation_layout = QVBoxLayout(conversation_panel)
        conversation_layout.setContentsMargins(1, 1, 1, 1)

        self.chat_container = QWidget()
        self.chat_container.setObjectName("chatContainer")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(18, 16, 18, 16)
        self.chat_layout.setSpacing(16)
        self.empty_state = self._build_empty_state()
        self.chat_layout.addWidget(self.empty_state, 1)
        self.chat_layout.addStretch(1)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setWidget(self.chat_container)
        self.chat_scroll.verticalScrollBar().rangeChanged.connect(
            self._scroll_chat_to_bottom
        )
        conversation_layout.addWidget(self.chat_scroll)
        layout.addWidget(conversation_panel, 1)

        composer_row = QHBoxLayout()
        composer_panel = QFrame()
        composer_panel.setObjectName("composerPanel")
        composer_layout = QHBoxLayout(composer_panel)
        composer_layout.setContentsMargins(10, 6, 8, 6)
        self.composer = MessageInput()
        self.composer.setPlaceholderText(
            "이 창에서 별도 작업을 입력하세요 · 작업 중 Enter: 대기열 추가"
        )
        self.composer.setToolTip(
            "Enter: 전송 · Shift+Enter: 줄바꿈\n"
            "작업이 실행 중일 때 Enter를 누르면 대기열에 추가되고, "
            "현재 작업이 끝난 뒤 순서대로 실행됩니다."
        )
        self.composer.setFixedHeight(54)
        self.composer.send_requested.connect(self.submit)
        composer_layout.addWidget(self.composer, 1)
        self.send_button = QPushButton("전송")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(96, 42)
        self.send_button.clicked.connect(self._handle_send_button)
        composer_layout.addWidget(self.send_button)
        composer_row.addWidget(composer_panel, 1)
        layout.addLayout(composer_row)

        hint = QLabel(
            "메인 창과 별개로 동시에 실행됩니다 · 중지: 현재 작업과 대기 작업 취소"
        )
        hint.setStyleSheet(f"color: {MUTED}; font-size: 10px; padding-right: 4px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(hint)
        self.composer.setFocus()

    def _build_empty_state(self) -> QWidget:
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(12, 12, 12, 12)
        empty_layout.addStretch(1)
        mark = QLabel("✦")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet("color: #82b8ac; font-size: 26px; font-weight: 400;")
        title = QLabel("새 작업 창")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 19px; font-weight: 700;")
        copy = QLabel("메인 창과 동시에 다른 작업을 진행할 수 있습니다.")
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        empty_layout.addWidget(mark)
        empty_layout.addSpacing(6)
        empty_layout.addWidget(title)
        empty_layout.addWidget(copy)
        empty_layout.addStretch(1)
        return empty

    # ---------------------------------------------------------- 요청 처리
    def submit(self) -> None:
        if self.closing:
            return
        selected_model = self.model_selector.currentText().strip()
        if selected_model:
            self.model = selected_model
        prompt = self.composer.toPlainText().strip()
        if not prompt:
            return
        if prompt.startswith("/"):
            self.composer.clear()
            self._add_message("user", prompt)
            result = self._run_command(prompt)
            self._add_message("assistant", result)
            return
        if not self.model:
            self._add_message(
                "assistant", "사용할 모델을 선택하거나 모델 이름을 입력해 주세요."
            )
            return
        if self.busy:
            self.composer.clear()
            self._enqueue_prompt(prompt)
            return
        self.composer.clear()
        self._begin_prompt(prompt)

    def _enqueue_prompt(self, prompt: str) -> None:
        self.pending_prompts.append((prompt, ""))
        preview = prompt if len(prompt) <= 60 else prompt[:57] + "..."
        self._add_activity_message(
            f"다음 작업을 대기열에 추가했습니다 "
            f"({len(self.pending_prompts)}개): {preview}"
        )
        self._refresh_status()

    def _start_next_pending_prompt(self) -> bool:
        if self.closing or self.busy:
            return False
        while self.pending_prompts:
            prompt, _audio = self.pending_prompts.popleft()
            if self._begin_prompt(prompt):
                return True
        self._refresh_status()
        return False

    def _begin_prompt(self, prompt: str) -> bool:
        if self.closing:
            return False
        try:
            request_client, request_model = agent.create_client(
                self.app.provider,
                self.model,
            )
        except Exception as error:
            self._add_message(
                "assistant",
                f"요청 연결을 준비하지 못했습니다: {type(error).__name__}: {error}",
            )
            return False
        self.composer.clear()
        self._add_message("user", prompt)
        self.last_activity_message = None
        self._add_activity_message("요청을 분석하고 있습니다...")
        request_messages = list(self.messages)
        cancel_event = threading.Event()
        self.request_cancel_event = cancel_event
        self.request_client = request_client
        self.request_cancel_pending = False
        self.busy = True
        self._set_busy(True)
        worker = threading.Thread(
            target=self._request_worker,
            args=(
                request_client,
                request_model,
                prompt,
                request_messages,
                cancel_event,
            ),
            daemon=True,
        )
        self.worker_thread = worker
        worker.start()
        return True

    def _request_worker(
        self,
        client: Any,
        model: str,
        prompt: str,
        messages: list[dict[str, Any]],
        cancel_event: threading.Event,
    ) -> None:
        # 이 스레드에서 실행되는 도구 메시지·승인이 이 작업 창으로 가도록 표시한다.
        self.app._tool_context.session = self
        try:
            try:
                response = agent.request_agent(
                    client,
                    model,
                    messages,
                    prompt,
                    live_analysis_server=getattr(
                        self.app, "live_analysis_server", None
                    ),
                    mcp_manager=getattr(self.app, "mcp_manager", None),
                    progress_callback=self.signals.request_progress.emit,
                    cancel_event=cancel_event,
                    max_tool_rounds=self.app.max_tool_rounds,
                    tool_calling_mode=self.app.tool_calling_mode,
                )
            except InterruptedError:
                self.signals.request_cancelled.emit()
                return
            except Exception as error:
                if cancel_event.is_set():
                    self.signals.request_cancelled.emit()
                    return
                self.signals.request_failed.emit(
                    f"{type(error).__name__}: {error}"
                )
                return
            if cancel_event.is_set():
                self.signals.request_cancelled.emit()
                return
            self.signals.response_ready.emit(response, messages)
        finally:
            self.app._tool_context.session = None
            try:
                client.close()
            except Exception:
                pass

    def _handle_send_button(self) -> None:
        if self.busy:
            self._cancel_request()
        else:
            self.submit()

    def _cancel_request(self) -> None:
        if (
            not self.busy
            or self.request_cancel_event is None
            or self.request_cancel_pending
        ):
            return
        self.request_cancel_pending = True
        self.request_cancel_event.set()
        self.send_button.setText("중지 중...")
        self.send_button.setEnabled(False)
        self.status_label.setText("응답을 중지하고 있습니다...")
        if self.request_client is not None:
            try:
                self.request_client.close()
            except Exception as error:
                self.status_label.setText(
                    f"요청 연결 중지 실패: {type(error).__name__}: {error}"
                )

    def _clear_request_state(self) -> None:
        self.request_cancel_event = None
        self.request_client = None
        self.request_cancel_pending = False

    def _finish_with_response(
        self,
        response: str,
        request_messages: list[dict[str, Any]],
    ) -> None:
        if self.closing:
            return
        self.messages[:] = request_messages
        self._clear_request_state()
        self._add_message("assistant", response)
        self._set_busy(False)
        if self._start_next_pending_prompt():
            return

    def _finish_with_error(self, error: str) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self._add_message("assistant", f"요청 처리 실패: {error}")
        self._set_busy(False)
        self._start_next_pending_prompt()

    def _finish_cancelled_request(self) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self.pending_prompts.clear()
        self._add_message("assistant", "응답 생성을 중지했습니다.")
        self._set_busy(False)

    def _run_command(self, command: str) -> str:
        output: list[str] = []
        try:
            agent.handle_cli_command(
                command,
                self.messages,
                collaboration_monitor=None,
                output=output.append,
            )
        except Exception as error:
            return f"명령 실행 실패: {type(error).__name__}: {error}"
        if command.strip().lower() == "/clear":
            self._clear_chat_view()
        return "\n".join(output).strip() or "명령을 실행했습니다."

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.setText("중지" if busy else "전송")
        self.send_button.setEnabled(True)
        self.progress.setVisible(busy)
        if busy:
            self.status_label.setText("응답을 생성하는 중...")
        else:
            self._refresh_status()

    def _refresh_status(self) -> None:
        if self.closing:
            return
        if self.busy:
            pending = (
                f" · 대기 {len(self.pending_prompts)}개"
                if self.pending_prompts
                else ""
            )
            self.status_label.setText(f"작업을 실행하는 중입니다{pending}")
        elif self.pending_prompts:
            self.status_label.setText(
                f"대기 중인 작업 {len(self.pending_prompts)}개 · "
                "현재 작업이 끝나면 순서대로 실행합니다."
            )
        else:
            self.status_label.setText(
                f"준비됨 · 모델 {self.model or '선택 안 됨'}"
            )

    def _show_request_progress(self, progress: str) -> None:
        if self.closing or self.request_cancel_pending:
            return
        self._add_activity_message(progress)
        self.status_label.setText(progress)

    def _show_agent_message(self, message: str, kind: str) -> None:
        if self.closing or self.request_cancel_pending:
            return
        self.last_activity_message = None
        content = f"**오류 및 복구**\n\n{message}" if kind == "error" else message
        self._add_message("assistant", content)

    def _show_tool_approval(
        self,
        name: str,
        details: str,
        request: dict[str, Any],
    ) -> None:
        event = request.get("event")
        if not isinstance(event, threading.Event):
            return
        if self.closing:
            event.set()
            return
        self.raise_()
        self.activateWindow()
        answer = QMessageBox.warning(
            self,
            "위험 작업 승인",
            f"도구 '{name}'이(가) 다음 작업을 요청했습니다.\n\n{details}\n\n실행할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        request["approved"] = answer == QMessageBox.StandardButton.Yes
        event.set()

    # ------------------------------------------------------------ 채팅 뷰
    def _add_message(
        self,
        role: str,
        content: str,
        streaming: bool = False,
    ) -> tuple[QFrame, QLabel]:
        if self.empty_state is not None:
            self.chat_layout.removeWidget(self.empty_state)
            self.empty_state.deleteLater()
            self.empty_state = None

        bubble = QFrame()
        bubble.setObjectName("messageBubble")
        bubble.setStyleSheet(
            "#messageBubble {"
            f"background: {'#12192a' if role == 'assistant' else '#1a1b36'};"
            f"border: 1px solid {'#29314a' if role == 'assistant' else '#34345d'};"
            "border-radius: 16px;"
            "}"
        )
        bubble.setMaximumWidth(760)
        bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(18, 14, 18, 14)
        bubble_layout.setSpacing(7)

        role_label = QLabel("QUASAR" if role == "assistant" else "YOU")
        role_label.setStyleSheet(
            f"color: {ACCENT if role == 'assistant' else '#aaa3e8'};"
            "font-size: 10px; font-weight: 800; letter-spacing: 0.5px;"
        )
        body = QLabel()
        body.setTextFormat(
            Qt.TextFormat.PlainText if streaming else Qt.TextFormat.MarkdownText
        )
        body.setText(content)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size: 13px; line-height: 1.45;")
        bubble_layout.addWidget(role_label)
        bubble_layout.addWidget(body)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if role == "assistant":
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            row.addStretch(1)
            row.addWidget(bubble)
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)
        return bubble, body

    def _add_activity_message(self, content: str) -> None:
        if self.last_activity_message == content:
            return
        self.last_activity_message = content

        message = QLabel(content)
        message.setObjectName("activityMessage")
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; padding: 2px 6px;"
        )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(message)
        row.addStretch(1)
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _clear_chat_view(self) -> None:
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
                child_layout.deleteLater()
            elif item.widget() is not None:
                item.widget().deleteLater()

    def _scroll_chat_to_bottom(self, *_range: int) -> None:
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: Any) -> None:
        self.closing = True
        self.pending_prompts.clear()
        if self.request_cancel_event is not None:
            self.request_cancel_event.set()
        if self.request_client is not None:
            try:
                self.request_client.close()
            except Exception:
                pass
        event.accept()


def main() -> None:
    if sys.platform == "win32":
        set_app_user_model_id = ctypes.WinDLL(
            "shell32",
            use_last_error=True,
        ).SetCurrentProcessExplicitAppUserModelID
        set_app_user_model_id.argtypes = [ctypes.c_wchar_p]
        set_app_user_model_id.restype = ctypes.c_long
        result = set_app_user_model_id(WINDOWS_APP_USER_MODEL_ID)
        if result != 0:
            raise OSError(
                result,
                "Quasar 작업 표시줄 앱 ID를 설정하지 못했습니다.",
            )

    app = QApplication([])
    app.setApplicationName("Quasar")
    app.setApplicationDisplayName("Quasar")
    app.setWindowIcon(
        QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "quasar_icon.png"))
    )
    window = QuasarDesktopApp()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
