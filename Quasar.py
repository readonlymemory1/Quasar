from __future__ import annotations

import csv
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

import quasar_cli as agent
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
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
    QFrame,
    QHBoxLayout,
    QLabel,
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
    "당신은 Quasar입니다. 한국어로 간결하고 정확하게 답하세요. "
    "필요하면 제공된 도구를 사용하되, 요청하지 않은 시스템 변경이나 외부 작업은 하지 마세요. "
    "명령 실행은 사용자의 별도 승인을 받아야 합니다. 이메일은 사용자가 명시적으로 요청한 경우에만 발송하고, "
    "요청이 모호하면 먼저 확인하세요. 도구 결과를 확인하기 전에는 성공했다고 말하지 마세요."
)

NEMO_SPEECH_MODEL = "nemotron-3.5"


class AppSignals(QObject):
    response_ready = Signal(str)
    request_failed = Signal(str)
    approval_requested = Signal(str, str, object)
    models_loaded = Signal(str, object, object)
    voice_status = Signal(str)
    voice_text_ready = Signal(str)
    voice_failed = Signal(str)
    speech_finished = Signal()
    speech_failed = Signal(str)


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

class LocalSpeechRecognizer:
    def __init__(self) -> None:
        self.model_lock = threading.Lock()
        self.runtime_environment: dict[str, str] | None = None
        self.runtime_temp_dir: Path | None = None

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

    def transcribe(self, audio: Any, signals: AppSignals) -> None:
        with self.model_lock:
            import numpy as np

            if self.runtime_environment is None or self.runtime_temp_dir is None:
                (
                    self.runtime_environment,
                    self.runtime_temp_dir,
                ) = self._prepare_runtime_paths()
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
            signals.voice_status.emit(
                "Nemotron Q8 양자화 모델로 음성을 인식하는 중입니다. "
                "첫 실행은 모델을 다운로드할 수 있습니다..."
            )
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
                    wav_file.setframerate(16_000)
                    wav_file.writeframes(pcm_samples.tobytes())

                try:
                    result = subprocess.run(
                        [
                            executable,
                            "transcribe",
                            str(audio_path),
                            "--model",
                            NEMO_SPEECH_MODEL,
                            "--language",
                            "ko-KR",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=900,
                        check=False,
                        env=self.runtime_environment,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "양자화 음성 인식이 15분 내에 끝나지 않았습니다."
                    ) from error

            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"Nemotron 양자화 음성 인식 실패: {detail or result.returncode}"
                )
            transcript = result.stdout.strip()

        if not transcript:
            raise RuntimeError("음성을 인식하지 못했습니다. 다시 말씀해 주세요.")
        signals.voice_text_ready.emit(transcript)


class VoiceCaptureWorker(threading.Thread):
    def __init__(
        self,
        stop_event: threading.Event,
        recognizer: LocalSpeechRecognizer,
        signals: AppSignals,
    ) -> None:
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.recognizer = recognizer
        self.signals = signals

    def run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            input_device = sd.query_devices(kind="input")
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

            samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
            samples /= 32768.0
            target_rate = 16_000
            if sample_rate != target_rate:
                target_count = round(len(samples) * target_rate / sample_rate)
                target_positions = np.arange(target_count) * sample_rate / target_rate
                samples = np.interp(
                    target_positions,
                    np.arange(len(samples)),
                    samples,
                ).astype(np.float32)
            peak = float(np.max(np.abs(samples)))
            if 0 < peak < 0.7:
                samples = np.clip(samples * min(4.0, 0.7 / peak), -1.0, 1.0)

            self.signals.voice_status.emit("녹음한 음성을 로컬에서 인식하는 중입니다...")
            self.recognizer.transcribe(samples, self.signals)
        except Exception as error:
            self.signals.voice_failed.emit(f"{type(error).__name__}: {error}")


class LocalSpeechOutputWorker(threading.Thread):
    def __init__(self, text: str, signals: AppSignals) -> None:
        super().__init__(daemon=True)
        self.text = text
        self.signals = signals

    def run(self) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init("sapi5")
            voices = engine.getProperty("voices")
            korean_voice = next(
                (
                    voice
                    for voice in voices
                    if any(
                        name in voice.name.casefold()
                        for name in ("korean", "heami", "huihui")
                    )
                ),
                None,
            )
            if korean_voice is not None:
                engine.setProperty("voice", korean_voice.id)
            engine.say(self.text)
            engine.runAndWait()
            engine.stop()
        except Exception as error:
            self.signals.speech_failed.emit(f"{type(error).__name__}: {error}")
            return
        self.signals.speech_finished.emit()


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
        self.busy = False
        self.closing = False
        self.pending_response: str | None = None
        self.response_body: QLabel | None = None
        self.response_position = 0
        self.pending_approvals: list[dict[str, Any]] = []
        self.voice_recognizer = LocalSpeechRecognizer()
        self.voice_recording = False
        self.voice_processing = False
        self.voice_stop_event: threading.Event | None = None
        self.voice_worker: VoiceCaptureWorker | None = None
        self.speech_worker: LocalSpeechOutputWorker | None = None
        self.signals = AppSignals(self)
        self.signals.response_ready.connect(self._finish_with_response)
        self.signals.request_failed.connect(self._finish_with_error)
        self.signals.approval_requested.connect(self._show_command_approval)
        self.signals.models_loaded.connect(self._apply_model_options)
        self.signals.voice_status.connect(self._show_voice_status)
        self.signals.voice_text_ready.connect(self._submit_voice_text)
        self.signals.voice_failed.connect(self._finish_voice_error)
        self.signals.speech_finished.connect(self._finish_speech)
        self.signals.speech_failed.connect(self._finish_speech_error)

        self.provider = os.getenv("AI_PROVIDER", "ollama").lower()
        self.client: Any = None
        self.model = ""
        self.startup_error: str | None = None
        self._create_client(self.provider)
        self.original_run_tool = agent.run_tool
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
        layout.addLayout(conversation_row, 1)

        composer_row = QHBoxLayout()
        composer_row.setContentsMargins(0, 0, 0, 0)
        composer_row.setSpacing(12)
        composer_panel = QFrame()
        composer_panel.setObjectName("composerPanel")
        composer_layout = QHBoxLayout(composer_panel)
        composer_layout.setContentsMargins(10, 6, 8, 6)
        self.composer = MessageInput()
        self.composer.setPlaceholderText("무엇을 도와드릴까요?")
        self.composer.setFixedHeight(58)
        self.composer.send_requested.connect(self.submit)
        composer_layout.addWidget(self.composer, 1)

        self.send_button = QPushButton("전송")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(96, 44)
        self.send_button.clicked.connect(self.submit)
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
        self.voice_response_toggle = QCheckBox("답변 음성")
        self.voice_response_toggle.setChecked(True)
        self.voice_response_toggle.setToolTip("답변을 Windows 음성으로 읽습니다.")
        hint_row.addWidget(self.voice_response_toggle)
        hint_row.addStretch(1)
        composer_hint = QLabel("Space 길게 누르기: 말하기  ·  Enter: 전송")
        composer_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        composer_hint.setStyleSheet(f"color: {MUTED}; font-size: 10px; padding-right: 4px;")
        hint_row.addWidget(composer_hint)
        layout.addLayout(hint_row)
        self.black_hole_overlay = BlackHoleOverlay(central)
        self.black_hole_overlay.setGeometry(central.rect())
        self.galaxy_transition = GalaxyTransitionOverlay(
            central,
            self.orb,
            self.orb_column,
        )
        self.orb.clicked.connect(self.galaxy_transition.toggle)
        self.response_timer = QTimer(self)
        self.response_timer.setInterval(16)
        self.response_timer.timeout.connect(self._stream_response_chunk)
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
        self._create_client(provider)
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        self.model_selector.setEditText(self.model)
        self.model_selector.blockSignals(False)
        self.refresh_models_button.setEnabled(self.client is not None)
        self._refresh_status()
        if self.client is not None:
            self._load_model_options()

    def _remember_model(self, model: str) -> None:
        model = model.strip()
        if model:
            self.model = model

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
            self.status_label.setText(
                f"모델 목록을 모두 불러오지 못했습니다. 이름을 직접 입력할 수 있습니다. "
                f"({errors[0]})"
            )
        else:
            self._refresh_status()

    def _refresh_status(self) -> None:
        if self.startup_error:
            self.status_label.setText(
                f"AI 연결 설정을 확인해 주세요: {self.startup_error}"
            )
        elif self.client is not None:
            self.status_label.setText(
                f"{self.provider.upper()} · {self.model or '모델을 선택하세요'}"
            )

    def _configure_agent(self) -> None:
        agent.current_directory = Path(__file__).resolve().parent
        agent.auto_approve_commands = False

        def run_tool_with_approval(
            name: str,
            arguments: dict[str, Any],
            live_analysis_server: Any = None,
            mcp_manager: Any = None,
        ) -> Any:
            if name in {"execute_cmd", "schedule_command"}:
                command = arguments.get("command", "")
                action = "실행" if name == "execute_cmd" else "예약"
                if not isinstance(command, str):
                    return "명령이 문자열이 아닙니다."
                if not self._request_command_approval(command, action):
                    return f"명령 {action}이 사용자에 의해 거부되었습니다."
                previous = agent.auto_approve_commands
                agent.auto_approve_commands = True
                try:
                    return self.original_run_tool(
                        name, arguments, live_analysis_server, mcp_manager
                    )
                finally:
                    agent.auto_approve_commands = previous
            return self.original_run_tool(
                name, arguments, live_analysis_server, mcp_manager
            )

        agent.run_tool = run_tool_with_approval

    def submit(self) -> None:
        if self.busy:
            return
        selected_model = self.model_selector.currentText().strip()
        if selected_model:
            self.model = selected_model
        prompt = self.composer.toPlainText().strip()
        if not prompt:
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
        self.messages.append({"role": "user", "content": prompt})
        self._add_message("user", prompt)
        self.busy = True
        self._set_busy(True)
        client = self.client
        model = self.model
        worker = threading.Thread(
            target=self._request_response,
            args=(client, model, prompt),
            daemon=True,
        )
        self.worker_thread = worker
        worker.start()

    def _request_response(self, client: Any, model: str, prompt: str) -> None:
        try:
            response = agent.request_agent(
                client,
                model,
                self.messages,
                prompt,
                live_analysis_server=None,
                mcp_manager=None,
            )
        except Exception as error:
            self.signals.request_failed.emit(
                f"{type(error).__name__}: {error}"
            )
            return
        self.signals.response_ready.emit(response)

    def _start_voice_recording(self) -> None:
        if self.busy or self.voice_recording or self.voice_processing:
            return
        self.voice_recording = True
        self.voice_stop_event = threading.Event()
        self.voice_button.setText("듣는 중... 놓으면 전송")
        self.status_label.setText("마이크를 통해 듣고 있습니다...")
        worker = VoiceCaptureWorker(
            self.voice_stop_event,
            self.voice_recognizer,
            self.signals,
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

    def _finish_voice_error(self, error: str) -> None:
        if self.closing:
            return
        self.voice_recording = False
        self.voice_processing = False
        self.voice_button.setEnabled(not self.busy)
        self.voice_button.setText("누르고 말하기")
        self.status_label.setText(f"음성 입력 실패: {error}")
        self.voice_button.setFocus()

    def _speak_response(self, response: str) -> None:
        spoken_text = re.sub(r"```.*?```", "코드 블록", response, flags=re.DOTALL)
        spoken_text = re.sub(r"[*_#`>]", "", spoken_text).strip()
        if not spoken_text:
            return
        self.status_label.setText("답변을 음성으로 읽고 있습니다...")
        worker = LocalSpeechOutputWorker(spoken_text, self.signals)
        self.speech_worker = worker
        worker.start()

    def _finish_speech(self) -> None:
        if not self.closing and not self.busy:
            self.status_label.setText("음성 출력을 마쳤습니다.")

    def _finish_speech_error(self, error: str) -> None:
        if not self.closing:
            self.status_label.setText(f"음성 출력 실패: {error}")

    def _finish_with_response(self, response: str) -> None:
        if self.closing:
            return
        self.pending_response = response
        _bubble, self.response_body = self._add_message(
            "assistant", "", streaming=True
        )
        self.response_body.setTextFormat(Qt.TextFormat.PlainText)
        self.response_position = 0
        self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        )
        self.response_timer.start()

    def _stream_response_chunk(self) -> None:
        if self.closing or self.pending_response is None or self.response_body is None:
            self.response_timer.stop()
            return
        if self.response_position < len(self.pending_response):
            next_position = min(
                self.response_position + 3, len(self.pending_response)
            )
            self.response_body.setText(
                self.pending_response[:next_position]
            )
            self.response_position = next_position
            self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            )
            return

        self.response_timer.stop()
        self.response_body.setTextFormat(Qt.TextFormat.MarkdownText)
        self.response_body.setText(self.pending_response)
        completed_response = self.pending_response
        self.pending_response = None
        self.response_body = None
        self._set_busy(False)
        if self.voice_response_toggle.isChecked():
            self._speak_response(completed_response)

    def _finish_with_error(self, error: str) -> None:
        if self.closing:
            return
        self._add_message("assistant", f"요청 처리 실패: {error}")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.setEnabled(not busy)
        self.provider_selector.setEnabled(not busy)
        self.model_selector.setEnabled(not busy)
        self.refresh_models_button.setEnabled(not busy)
        self.voice_button.setEnabled(not busy and not self.voice_processing)
        self.progress.setVisible(busy)
        self.orb.set_active(busy)
        if busy:
            self.status_label.setText("응답을 생성하는 중...")
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
        self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        )
        return bubble, body

    def _request_command_approval(self, command: str, action: str) -> bool:
        decision: dict[str, Any] = {"approved": False, "event": threading.Event()}
        self.pending_approvals.append(decision)
        self.signals.approval_requested.emit(command, action, decision)
        decision["event"].wait()
        if decision in self.pending_approvals:
            self.pending_approvals.remove(decision)
        return bool(decision["approved"]) and not self.closing

    def _show_command_approval(
        self,
        command: str,
        action: str,
        decision: dict[str, Any],
    ) -> None:
        if self.closing:
            decision["event"].set()
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle(f"명령 {action} 승인")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"이 명령을 {action}할까요?")
        dialog.setInformativeText(command)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        decision["approved"] = (
            dialog.exec() == QMessageBox.StandardButton.Yes
        )
        decision["event"].set()

    def closeEvent(self, event: Any) -> None:
        if self.busy:
            answer = QMessageBox.question(
                self,
                "Quasar 종료",
                "요청 처리 중입니다. 종료하면 현재 요청이 끝나지 않을 수 있습니다.\n"
                "종료할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.closing = True
        if self.voice_stop_event is not None:
            self.voice_stop_event.set()
        agent.run_tool = self.original_run_tool
        for decision in self.pending_approvals:
            decision["approved"] = False
            decision["event"].set()
        event.accept()


def main() -> None:
    app = QApplication([])
    window = QuasarDesktopApp()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
