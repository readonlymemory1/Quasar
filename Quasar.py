from __future__ import annotations

import csv
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
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import quasar_cli as agent
from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from quasar_galaxy import (
    CANVAS_BACKGROUND_STOPS,
    BlackHoleOverlay,
    GalaxyTransitionOverlay,
    OrbWidget,
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
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


BACKGROUND = "#070a16"
TEXT = "#e7eaff"
MUTED = "#929bb8"
ACCENT = "#9b91ff"

SYSTEM_PROMPT = (
    "You are Quasar. Respond concisely and accurately in the language the user uses, "
    "especially Korean or English. "
    "Use message_to_user proactively as a live status channel. For multi-step work, send a brief progress update before the first lengthy operation, "
    "then report each meaningful phase completion and the next step before another lengthy wait; do not wait until the final answer. "
    "For several quick related actions, combine them into one update rather than narrating every tool call. "
    "If a tool fails or blocks progress, immediately use kind='error' to state the observed cause and your concrete recovery attempt; "
    "after recovery, report the outcome and continue verifying. Keep updates brief, factual, and actionable; avoid duplicates and routine narration "
    "for simple questions or quick actions. Do not claim completion until verified. "
    "When the user asks to turn spoken responses on or off, use the set_tts_enabled tool. "
    "Use the provided tools when needed, but do not make system changes or perform external actions that were not requested. "
    "Run requested commands without showing confirmation dialogs. "
    "Send email only when explicitly requested, and clarify ambiguous requests first. "
    "For browser tasks, navigate to the requested page once, then continue interacting with the current page. "
    "Do not navigate to or reload the same URL again unless the user explicitly asks to refresh or revisit it. "
    "Before interacting with a web page, inspect its DOM using the browser snapshot or page-evaluation tools. "
    "If the DOM does not provide enough information to understand the page or choose a target, take and inspect a screenshot "
    "before acting. Reinspect the DOM after navigation or a meaningful page-state change before continuing. "
    "When needed, discover browser tools with an English query mentioning DOM snapshot, page evaluation, and screenshot. "
    "When asked to create or edit a PowerPoint presentation, discover and use the connected PowerPoint MCP tools "
    "with an English query such as 'create and build PowerPoint presentations, slides, pptx files'. "
    "Do not claim PowerPoint generation is unavailable unless tool discovery or execution confirms that it is. "
    "Do not claim success until you have verified the tool result."
)

NEMO_SPEECH_MODEL = "nemotron-3.5"
SPEECH_OUTPUT_GAIN = 3.0
VOICE_RECOGNITION_SAMPLE_RATE = 16_000
VOICE_INPUT_MAX_GAIN = 2.0
VOICE_INPUT_TARGET_PEAK = 0.35
VOICE_CONVERSATION_END_SILENCE_SECONDS = 0.6


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
    voice_failed = Signal(str)
    conversation_voice_text_ready = Signal(str)
    conversation_voice_failed = Signal(str)
    speech_finished = Signal(object)
    speech_failed = Signal(object, str)
    tts_enabled_requested = Signal(bool)
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


class MicrophoneSettingsDialog(QDialog):
    def __init__(
        self,
        selected_device_index: int | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.microphone_selector = QComboBox()
        form.addRow("입력 마이크", self.microphone_selector)
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

    @property
    def selected_device_index(self) -> int | None:
        device_index = self.microphone_selector.currentData()
        if isinstance(device_index, int) and device_index >= 0:
            return device_index
        return None

    @property
    def selected_device_name(self) -> str:
        return self.microphone_selector.currentText()

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


class VoiceCaptureWorker(threading.Thread):
    def __init__(
        self,
        stop_event: threading.Event,
        recognizer: LocalSpeechRecognizer,
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

            input_device = (
                sd.query_devices(kind="input")
                if self.device_index is None
                else sd.query_devices(self.device_index)
            )
            if int(input_device["max_input_channels"]) < 1:
                raise RuntimeError(
                    "선택한 장치는 마이크 입력을 지원하지 않습니다. "
                    "설정에서 입력 마이크를 다시 선택해 주세요."
                )
            sample_rate = int(input_device["default_samplerate"])
            if sample_rate <= 0:
                raise RuntimeError("사용 가능한 마이크 샘플링 속도를 확인할 수 없습니다.")
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

            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=capture_audio,
                device=self.device_index,
            ):
                self.signals.voice_status.emit("듣는 중... 스페이스를 놓으면 전송합니다.")
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
        recognizer: LocalSpeechRecognizer,
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

            input_device = (
                sd.query_devices(kind="input")
                if self.device_index is None
                else sd.query_devices(self.device_index)
            )
            if int(input_device["max_input_channels"]) < 1:
                raise RuntimeError(
                    "선택한 장치는 마이크 입력을 지원하지 않습니다. "
                    "설정에서 입력 마이크를 다시 선택해 주세요."
                )
            sample_rate = int(input_device["default_samplerate"])
            if sample_rate <= 0:
                raise RuntimeError("사용 가능한 마이크 샘플링 속도를 확인할 수 없습니다.")

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

            self.signals.voice_status.emit(
                "듣는 중... 편하게 말씀하시면 자동으로 전송합니다."
            )
            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=capture_audio,
                device=self.device_index,
            ):
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

            input_device = (
                sd.query_devices(kind="input")
                if self.device_index is None
                else sd.query_devices(self.device_index)
            )
            if int(input_device["max_input_channels"]) < 1:
                raise RuntimeError("선택한 장치는 마이크 입력을 지원하지 않습니다.")
            sample_rate = int(input_device["default_samplerate"])
            if sample_rate <= 0:
                raise RuntimeError("마이크 샘플링 속도를 확인할 수 없습니다.")
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

            with sd.RawInputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=capture_audio,
                device=self.device_index,
            ):
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
                        break
                    details = result.stderr.strip() or result.stdout.strip()
                    if attempt == 0 and "NoAudioReceived" in details:
                        time.sleep(1)
                        continue
                    raise RuntimeError(
                        f"Edge 음성 합성에 실패했습니다: {details or result.returncode}"
                    )

                with av.open(str(audio_path)) as container:
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
                if self.provider == "ollama"
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


class QuasarDesktopApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quasar")
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

        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.settings = QSettings("Quasar", "Quasar")
        self.office_monitor: agent.CliCollaborationMonitor | None = None
        self.office_monitor_client: Any = None
        self.office_monitor_source: _OfficeDocumentSource | None = None
        self.mcp_manager: agent.MCPServerManager | None = None
        self.mcp_startup_error: str | None = None
        self.microphone_notice: str | None = None
        self.microphone_device_index = self._restore_microphone_selection()
        self.busy = False
        self.worker_thread: threading.Thread | None = None
        self.closing = False
        self.request_cancel_pending = False
        self.request_cancel_event: threading.Event | None = None
        self.request_client: Any = None
        self.last_activity_message: str | None = None
        self.voice_recognizer = LocalSpeechRecognizer()
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
        self.signals = AppSignals(self)
        self.signals.response_ready.connect(self._finish_with_response)
        self.signals.request_cancelled.connect(self._finish_cancelled_request)
        self.signals.request_failed.connect(self._finish_with_error)
        self.signals.request_progress.connect(self._show_request_progress)
        self.signals.agent_message.connect(self._show_agent_message)
        self.signals.alarm_triggered.connect(self._show_alarm_notification)
        self.signals.models_loaded.connect(self._apply_model_options)
        self.signals.voice_status.connect(self._show_voice_status)
        self.signals.voice_text_ready.connect(self._submit_voice_text)
        self.signals.voice_failed.connect(self._finish_voice_error)
        self.signals.conversation_voice_text_ready.connect(
            self._submit_voice_conversation_text
        )
        self.signals.conversation_voice_failed.connect(
            self._finish_voice_conversation_error
        )
        self.signals.speech_finished.connect(self._finish_speech)
        self.signals.speech_failed.connect(self._finish_speech_error)
        self.signals.tts_enabled_requested.connect(self._set_tts_enabled)

        self.provider = os.getenv("AI_PROVIDER", "ollama").lower()
        self.client: Any = None
        self.model = ""
        self.startup_error: str | None = None
        self.original_alarm_notification_callback = (
            agent.alarm_notification_callback
        )
        self.alarm_notifications: list[QMessageBox] = []
        self._create_client(self.provider)
        self.original_run_tool = agent.run_tool
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
        header.setContentsMargins(18, 14, 16, 14)
        header.setSpacing(10)

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

        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
        title = QLabel("Quasar")
        title.setStyleSheet("font-size: 19px; font-weight: 750;")
        subtitle = QLabel("개인 AI 워크스페이스")
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        brand_copy.addWidget(title)
        brand_copy.addWidget(subtitle)
        header.addLayout(brand_copy)
        header.addStretch(1)

        self.provider_selector = QComboBox()
        self.provider_selector.addItems(("ollama", "openai"))
        self.provider_selector.setCurrentText(self.provider)
        self.provider_selector.currentTextChanged.connect(self._change_provider)
        self.provider_selector.setToolTip("AI 공급자 선택")
        self.provider_selector.setMinimumWidth(108)
        header.addWidget(self.provider_selector)

        self.model_selector = QComboBox()
        self.model_selector.setEditable(True)
        self.model_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_selector.setMinimumWidth(210)
        self.model_selector.setEditText(self.model)
        self.model_selector.setToolTip("모델 선택 또는 모델 이름 직접 입력")
        self.model_selector.currentTextChanged.connect(self._remember_model)
        header.addWidget(self.model_selector)

        self.refresh_models_button = QPushButton("모델 새로고침")
        self.refresh_models_button.setMinimumHeight(40)
        self.refresh_models_button.clicked.connect(self._load_model_options)
        header.addWidget(self.refresh_models_button)

        self.settings_button = QPushButton("설정")
        self.settings_button.setMinimumHeight(40)
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)
        self.office_watch_button = QPushButton("문서 감시")
        self.office_watch_button.setMinimumHeight(40)
        self.office_watch_button.clicked.connect(self._open_office_watch_dialog)
        self.office_watch_button.setToolTip(
            "저장된 Word/PPT/메모장 텍스트 파일을 공동작업 목록에 등록합니다."
        )
        header.addWidget(self.office_watch_button)
        self.checklist_button = QPushButton("체크리스트 열기")
        self.checklist_button.setMinimumHeight(40)
        self.checklist_button.setCheckable(True)
        self.checklist_button.toggled.connect(self._set_checklist_visible)
        header.addWidget(self.checklist_button)
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
        self.composer.setPlaceholderText("메시지 또는 / 명령어를 입력하세요")
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
            "Space 길게 누르기: 말하기  ·  Enter: 전송  ·  /help: 명령어 목록"
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

    def _create_client(self, provider: str) -> None:
        try:
            self.client, self.model = agent.create_client(provider)
            self.provider = provider
            self.startup_error = None
        except Exception as error:
            self.client = None
            self.model = ""
            self.provider = provider
            self.startup_error = str(error)

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

    def _open_settings(self) -> None:
        dialog = MicrophoneSettingsDialog(
            self.microphone_device_index,
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
                "마이크 선택을 저장하지 못했습니다. 다시 시도해 주세요.",
            )
            return
        self.microphone_device_index = selected_device_index
        self.microphone_notice = None
        self.status_label.setText(
            f"입력 마이크: {dialog.selected_device_name}"
        )

    def _load_model_options(self) -> None:
        if self.client is None or self.busy:
            return
        self.refresh_models_button.setEnabled(False)
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
            return
        if self.microphone_notice:
            status = f"{status} · {self.microphone_notice}"
        if self.mcp_startup_error:
            status = f"{status} · {self.mcp_startup_error}"
        self.status_label.setText(status)

    def _ensure_office_monitor(self) -> agent.CliCollaborationMonitor:
        if self.office_monitor is not None:
            return self.office_monitor
        model = self.model_selector.currentText().strip() or self.model
        if not model:
            raise ValueError("Office 감시를 시작하기 전에 AI 모델을 선택해 주세요.")
        client, selected_model = agent.create_client(self.provider, model)
        workspace = Path(__file__).resolve().parent
        source = _OfficeDocumentSource(workspace)
        settings = agent.load_collaboration_settings(
            workspace / "collaboration_config.json"
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
        workspace = Path(__file__).resolve().parent
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
        return result or "명령을 실행했습니다."

    def _configure_agent(self) -> None:
        agent.current_directory = Path(__file__).resolve().parent
        agent.auto_approve_commands = True
        agent.run_tool = self._run_tool
        if hasattr(self, "signals"):
            agent.alarm_notification_callback = self.signals.alarm_triggered.emit
        mcp_config_path = agent.resolve_mcp_config_path(
            None,
            agent.current_directory,
            Path(__file__).resolve().parent,
        )
        try:
            self.mcp_manager = agent.MCPServerManager(mcp_config_path)
        except (OSError, ValueError) as error:
            self.mcp_startup_error = (
                f"MCP 설정을 읽지 못했습니다 ({mcp_config_path}): {error}"
            )

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
            self.signals.agent_message.emit(message, kind)
            return "메시지를 사용자 채팅에 표시했습니다."
        return self.original_run_tool(
            name,
            arguments,
            live_analysis_server,
            mcp_manager,
        )

    def submit(self) -> None:
        if self.busy:
            return
        if self.speech_worker is not None:
            self._stop_speech()
        selected_model = self.model_selector.currentText().strip()
        if selected_model:
            self.model = selected_model
        prompt = self.composer.toPlainText().strip()
        if not prompt:
            return
        if prompt.startswith("/"):
            self.composer.clear()
            self._add_message("user", prompt)
            result = self._run_chat_command(prompt)
            self._add_message("assistant", result)
            return
        if self.client is None:
            self._add_message(
                "assistant",
                "AI 연결을 준비하지 못했습니다. 공급자 설정과 API 설정을 확인해 주세요.\n"
                f"상세 정보: {self.startup_error or '알 수 없는 초기화 오류'}",
            )
            return
        if not self.model:
            self._add_message("assistant", "사용할 모델을 선택하거나 모델 이름을 입력해 주세요.")
            return
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
            return

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
            ),
            daemon=True,
        )
        self.worker_thread = worker
        worker.start()

    def _request_response(
        self,
        client: Any,
        model: str,
        prompt: str,
        messages: list[dict[str, Any]],
        cancel_event: threading.Event,
        office_monitor: agent.CliCollaborationMonitor | None = None,
        mcp_manager: agent.MCPServerManager | None = None,
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
                response = agent.request_agent(
                    client,
                    model,
                    messages,
                    prompt,
                    live_analysis_server=None,
                    mcp_manager=mcp_manager,
                    progress_callback=self.signals.request_progress.emit,
                    cancel_event=cancel_event,
                    office_context=office_context,
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
            try:
                client.close()
            except Exception as error:
                print(
                    f"요청 연결 종료 실패: {type(error).__name__}: {error}",
                    file=sys.stderr,
                    flush=True,
                )

    def _handle_send_button(self) -> None:
        if self.busy:
            self._cancel_request()
        elif self.speech_worker is not None:
            self._stop_speech()
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
            else "Space 길게 누르기: 말하기  ·  Enter: 전송"
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
        spoken_text = re.sub(r"[*_#`>]", "", spoken_text).strip()
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
            or not self.voice_response_toggle.isChecked()
            or not message.strip()
        ):
            return
        self.pending_speech_messages.append(message.strip())
        self._start_next_pending_speech()

    def _start_next_pending_speech(self) -> bool:
        if self.closing or not self.voice_response_toggle.isChecked():
            self.pending_speech_messages.clear()
            return False
        if self.busy or self.speech_worker is not None:
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
        if not self.busy:
            self.send_button.setText("전송")
            self.send_button.setEnabled(True)
        self.status_label.setText("음성 출력을 중지했습니다.")
        if self.voice_conversation_active:
            self.galaxy_transition.set_conversation_status(
                "음성을 멈췄습니다. 말씀해 주세요."
            )
            self._start_voice_conversation_capture()

    def _finish_speech(self, worker: LocalSpeechOutputWorker) -> None:
        if self.closing or self.busy:
            return
        if worker is not self.speech_worker:
            return
        self.speech_worker = None
        self.send_button.setText("전송")
        self.send_button.setEnabled(True)
        if self._start_next_pending_speech():
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
        self.messages[:] = request_messages
        self._clear_request_state()
        self._add_message("assistant", response)
        self._set_busy(False)
        if self.voice_response_toggle.isChecked():
            if self._speak_response(response):
                return
        if self._start_next_pending_speech():
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()

    def _finish_with_error(self, error: str) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self._add_message("assistant", f"요청 처리 실패: {error}")
        self._set_busy(False)
        if self._start_next_pending_speech():
            return
        if self.voice_conversation_active:
            self._start_voice_conversation_capture()

    def _finish_cancelled_request(self) -> None:
        if self.closing:
            return
        self._clear_request_state()
        self.status_label.setText("응답 생성을 중지했습니다.")
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
        agent.run_tool = self.original_run_tool
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


def main() -> None:
    app = QApplication([])
    window = QuasarDesktopApp()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
