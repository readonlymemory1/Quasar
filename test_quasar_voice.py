import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import Quasar


class VoiceWorkerTests(unittest.TestCase):
    def test_capture_is_transcribed_locally_as_korean(self) -> None:
        captured: list[np.ndarray] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.voice_failed.connect(errors.append)

        class FakeInputStream:
            def __init__(self, **kwargs: object) -> None:
                self.callback = kwargs["callback"]

            def __enter__(self) -> "FakeInputStream":
                quiet_audio = np.full(44_100, 1_000, dtype=np.int16).tobytes()
                self.callback(quiet_audio, 44_100, None, None)
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda **_kwargs: {
            "default_samplerate": 44_100,
            "max_input_channels": 1,
        }
        sounddevice.RawInputStream = FakeInputStream

        class FakeRecognizer:
            def transcribe(self, audio: np.ndarray, _signals: object) -> None:
                captured.append(audio)

        stop_event = threading.Event()
        stop_event.set()

        worker = Quasar.VoiceCaptureWorker(
            stop_event,
            FakeRecognizer(),
            signals,
        )
        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            worker.run()

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].dtype, np.float32)
        self.assertEqual(captured[0].shape, (16_000,))
        self.assertTrue(0.06 < float(np.max(captured[0])) < 0.07)
        self.assertEqual(errors, [])

    def test_audio_input_test_plays_processed_microphone_audio(self) -> None:
        recorded: list[np.ndarray] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.audio_test_ready.connect(recorded.append)
        signals.audio_test_failed.connect(errors.append)

        class FakeInputStream:
            def __init__(self, **kwargs: object) -> None:
                self.callback = kwargs["callback"]

            def __enter__(self) -> "FakeInputStream":
                audio = np.full(44_100, 1_000, dtype=np.int16).tobytes()
                self.callback(audio, 44_100, None, None)
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda **_kwargs: {
            "default_samplerate": 44_100,
            "max_input_channels": 1,
        }
        sounddevice.RawInputStream = FakeInputStream
        stop_event = threading.Event()
        stop_event.set()

        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            Quasar.VoiceInputTestWorker(stop_event, signals, None).run()

        self.assertEqual(errors, [])
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].dtype, np.float32)
        self.assertEqual(recorded[0].shape, (16_000,))
        self.assertTrue(0.06 < float(np.max(recorded[0])) < 0.07)

        playback: dict[str, object] = {}
        playback_signals = Quasar.AppSignals()
        completed: list[bool] = []
        playback_signals.audio_test_playback_finished.connect(
            lambda: completed.append(True)
        )
        playback_device = types.ModuleType("sounddevice")
        playback_device.play = lambda samples, sample_rate: playback.update(
            samples=samples.copy(),
            sample_rate=sample_rate,
        )
        playback_device.get_stream = lambda: types.SimpleNamespace(active=False)
        playback_device.wait = lambda: None
        playback_device.stop = lambda: None

        with patch.dict(sys.modules, {"sounddevice": playback_device}):
            Quasar.VoicePlaybackTestWorker(
                recorded[0],
                threading.Event(),
                playback_signals,
            ).run()

        np.testing.assert_array_equal(playback["samples"], recorded[0])
        self.assertEqual(
            playback["sample_rate"],
            Quasar.VOICE_RECOGNITION_SAMPLE_RATE,
        )
        self.assertEqual(completed, [True])

    def test_capture_uses_selected_input_device(self) -> None:
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.voice_failed.connect(errors.append)
        calls: dict[str, object] = {}

        class FakeInputStream:
            def __init__(self, **kwargs: object) -> None:
                calls["stream_device"] = kwargs["device"]
                self.callback = kwargs["callback"]

            def __enter__(self) -> "FakeInputStream":
                audio = np.full(16_000, 1_000, dtype=np.int16).tobytes()
                self.callback(audio, 16_000, None, None)
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        sounddevice = types.ModuleType("sounddevice")

        def query_devices(
            device: int | None = None,
            *,
            kind: str | None = None,
        ) -> dict[str, int]:
            calls["query_device"] = device
            calls["query_kind"] = kind
            return {
                "default_samplerate": 16_000,
                "max_input_channels": 1,
            }

        sounddevice.query_devices = query_devices
        sounddevice.RawInputStream = FakeInputStream

        class FakeRecognizer:
            def transcribe(self, _audio: np.ndarray, _signals: object) -> None:
                return None

        stop_event = threading.Event()
        stop_event.set()
        worker = Quasar.VoiceCaptureWorker(
            stop_event,
            FakeRecognizer(),
            signals,
            device_index=2,
        )

        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            worker.run()

        self.assertEqual(calls["query_device"], 2)
        self.assertIsNone(calls["query_kind"])
        self.assertEqual(calls["stream_device"], 2)
        self.assertEqual(errors, [])

    def test_voice_input_gain_avoids_clipping_and_limits_noise_boost(self) -> None:
        quiet_audio = np.full(16_000, 1_000, dtype=np.int16).tobytes()
        loud_audio = np.full(16_000, 30_000, dtype=np.int16).tobytes()
        silence = np.zeros(16_000, dtype=np.int16).tobytes()

        quiet = Quasar.process_voice_input(quiet_audio, 16_000)
        loud = Quasar.process_voice_input(loud_audio, 16_000)
        silent = Quasar.process_voice_input(silence, 16_000)

        self.assertAlmostEqual(float(np.max(quiet)), 1000 / 32768 * 2, places=6)
        self.assertAlmostEqual(float(np.max(loud)), 30000 / 32768, places=6)
        self.assertLessEqual(float(np.max(np.abs(loud))), 1.0)
        self.assertEqual(float(np.max(np.abs(silent))), 0.0)

    def test_voice_input_resampling_rejects_aliasing_without_losing_speech_band(
        self,
    ) -> None:
        sample_rate = 48_000
        time_axis = np.arange(sample_rate, dtype=np.float32) / sample_rate
        speech_band = (0.5 * np.sin(2 * np.pi * 1_000 * time_axis)).astype(
            np.float32
        )
        out_of_band = (0.5 * np.sin(2 * np.pi * 12_000 * time_axis)).astype(
            np.float32
        )

        processed_speech = Quasar.process_voice_input(
            (speech_band * 32767).astype(np.int16).tobytes(),
            sample_rate,
        )
        processed_out_of_band = Quasar.process_voice_input(
            (out_of_band * 32767).astype(np.int16).tobytes(),
            sample_rate,
        )

        self.assertEqual(processed_speech.shape, (16_000,))
        self.assertGreater(float(np.sqrt(np.mean(processed_speech**2))), 0.3)
        self.assertLess(float(np.sqrt(np.mean(processed_out_of_band**2))), 0.01)

    def test_local_recognizer_starts_loopback_server_and_stops_it(self) -> None:
        process = Mock()
        process.poll.return_value = None
        client = Mock()
        recognizer = Quasar.LocalSpeechRecognizer()
        recognizer.server_client = client

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            recognizer,
            "_prepare_runtime_paths",
            return_value=({}, Path(temp_dir)),
        ), patch.object(
            recognizer,
            "_find_executable",
            return_value="nemo-speech",
        ), patch(
            "Quasar.subprocess.Popen",
            return_value=process,
        ) as popen:
            recognizer.start_server()
            command = popen.call_args.args[0]
            self.assertEqual(command[1:3], ["serve", "--host"])
            self.assertEqual(command[3], "127.0.0.1")
            self.assertIn("--no-ui", command)
            self.assertIn("--api-key", command)
            self.assertTrue(recognizer.server_api_key)
            self.assertIn(Quasar.NEMO_SPEECH_MODEL, command)
            self.assertTrue(recognizer.server_url.startswith("http://127.0.0.1:"))

            recognizer.close()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        client.close.assert_called_once_with()
        self.assertTrue(recognizer.closed)

    def test_selected_device_must_have_input_channels(self) -> None:
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.voice_failed.connect(errors.append)
        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda *_args, **_kwargs: {
            "default_samplerate": 48_000,
            "max_input_channels": 0,
        }

        class FakeRecognizer:
            def transcribe(self, _audio: np.ndarray, _signals: object) -> None:
                raise AssertionError("Output-only device must not be used")

        stop_event = threading.Event()
        worker = Quasar.VoiceCaptureWorker(
            stop_event,
            FakeRecognizer(),
            signals,
            device_index=8,
        )

        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            worker.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("마이크 입력을 지원하지 않습니다", errors[0])

    def test_saved_microphone_is_resolved_after_device_reordering(self) -> None:
        devices = [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
            {"name": "Preferred microphone", "hostapi": 1, "max_input_channels": 1},
            {"name": "Preferred microphone", "hostapi": 1, "max_input_channels": 2},
        ]

        self.assertEqual(
            Quasar.find_input_device_index(
                devices,
                "Preferred microphone",
                1,
                2,
            ),
            2,
        )
        self.assertIsNone(
            Quasar.find_input_device_index(devices, "Speakers", 0)
        )

    def test_voice_conversation_detects_speech_then_transcribes_after_silence(
        self,
    ) -> None:
        recognized: list[str] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.conversation_voice_text_ready.connect(recognized.append)
        signals.conversation_voice_failed.connect(errors.append)
        transcribed: list[np.ndarray] = []
        cancellations: list[threading.Event | None] = []

        class FakeInputStream:
            def __init__(self, **kwargs: object) -> None:
                self.callback = kwargs["callback"]

            def __enter__(self) -> "FakeInputStream":
                silence = np.zeros(1024, dtype="<i2").tobytes()
                speech = (
                    10_000
                    * np.sin(
                        2 * np.pi * 220 * np.arange(1024) / 16_000
                    )
                ).astype("<i2").tobytes()
                for block in [silence] * 5 + [speech] * 8 + [silence] * 10:
                    self.callback(block, 1024, None, None)
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        class FakeRecognizer:
            def transcribe(
                self,
                audio: np.ndarray,
                result_signals: Quasar.AppSignals,
                *,
                conversation: bool = False,
                stop_event: threading.Event | None = None,
            ) -> None:
                transcribed.append(audio)
                self.assert_conversation = conversation
                cancellations.append(stop_event)
                result_signals.conversation_voice_text_ready.emit("안녕하세요.")

        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda **_kwargs: {
            "default_samplerate": 16_000,
            "max_input_channels": 1,
        }
        sounddevice.RawInputStream = FakeInputStream
        recognizer = FakeRecognizer()
        worker = Quasar.VoiceConversationCaptureWorker(
            threading.Event(),
            recognizer,
            signals,
        )

        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            worker.run()

        self.assertEqual(recognized, ["안녕하세요."])
        self.assertEqual(errors, [])
        self.assertTrue(recognizer.assert_conversation)
        self.assertIs(cancellations[0], worker.stop_event)
        self.assertEqual(len(transcribed), 1)
        self.assertGreater(len(transcribed[0]), 16_000)
        self.assertAlmostEqual(
            Quasar.VOICE_CONVERSATION_END_SILENCE_SECONDS,
            0.6,
        )

    def test_voice_conversation_detects_quiet_speech_below_old_threshold(
        self,
    ) -> None:
        transcribed: list[np.ndarray] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.conversation_voice_failed.connect(errors.append)

        class FakeInputStream:
            def __init__(self, **kwargs: object) -> None:
                self.callback = kwargs["callback"]

            def __enter__(self) -> "FakeInputStream":
                silence = np.zeros(1024, dtype="<i2").tobytes()
                quiet_samples = (
                    220
                    * np.sin(
                        2
                        * np.pi
                        * 220
                        * np.arange(8 * 1024)
                        / 16_000
                    )
                ).astype("<i2")
                quiet_blocks = [
                    quiet_samples[index : index + 1024].tobytes()
                    for index in range(0, len(quiet_samples), 1024)
                ]
                blocks = [silence] * 5 + quiet_blocks + [silence] * 10
                for block in blocks:
                    self.callback(block, 1024, None, None)
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        class FakeRecognizer:
            def transcribe(
                self,
                audio: np.ndarray,
                _signals: Quasar.AppSignals,
                *,
                conversation: bool = False,
                stop_event: threading.Event | None = None,
            ) -> None:
                self.assert_conversation = conversation
                transcribed.append(audio)

        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda **_kwargs: {
            "default_samplerate": 16_000,
            "max_input_channels": 1,
        }
        sounddevice.RawInputStream = FakeInputStream
        recognizer = FakeRecognizer()

        with patch.dict(sys.modules, {"sounddevice": sounddevice}):
            Quasar.VoiceConversationCaptureWorker(
                threading.Event(),
                recognizer,
                signals,
            ).run()

        self.assertEqual(errors, [])
        self.assertTrue(recognizer.assert_conversation)
        self.assertEqual(len(transcribed), 1)

    def test_nemotron_model_auto_detects_korean_and_english(self) -> None:
        recognized: list[str] = []
        conversation_recognized: list[str] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.voice_text_ready.connect(recognized.append)
        signals.conversation_voice_text_ready.connect(conversation_recognized.append)
        signals.voice_failed.connect(errors.append)
        calls: list[dict[str, object]] = []
        outputs = [" 안녕하세요. ko-KR\n", " Hello there. en-US\n"]

        class FakeTranscriptions:
            def create(self, **kwargs: object) -> types.SimpleNamespace:
                audio_file = kwargs["file"]
                with wave.open(audio_file, "rb") as wav_file:
                    calls.append(
                        {
                            "model": kwargs["model"],
                            "wav_format": (
                    wav_file.getnchannels(),
                    wav_file.getsampwidth(),
                    wav_file.getframerate(),
                    wav_file.getnframes(),
                            ),
                        }
                    )
                return types.SimpleNamespace(text=outputs.pop(0))

        class FakeClient:
            def __init__(self) -> None:
                self.audio = types.SimpleNamespace(
                    transcriptions=FakeTranscriptions()
                )

        recognizer = Quasar.LocalSpeechRecognizer()
        recognizer.server_url = "http://127.0.0.1:12345"
        recognizer.server_model = ".nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
        recognizer.server_client = FakeClient()
        with patch.object(recognizer, "_wait_for_server"):
            recognizer.transcribe(np.full(16_000, 0.5, dtype=np.float32), signals)
            recognizer.transcribe(
                np.full(16_000, 0.5, dtype=np.float32),
                signals,
                conversation=True,
            )

        self.assertEqual(recognized, ["안녕하세요."])
        self.assertEqual(conversation_recognized, ["Hello there."])
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [call["model"] for call in calls],
            [recognizer.server_model, recognizer.server_model],
        )
        self.assertEqual(
            [call["wav_format"] for call in calls],
            [(1, 2, 16_000, 16_000), (1, 2, 16_000, 16_000)],
        )

    def test_voice_conversation_error_keeps_listening_and_schedules_retry(self) -> None:
        class FakeTimer:
            def __init__(self) -> None:
                self.delay: int | None = None

            def start(self, delay: int) -> None:
                self.delay = delay

        stop_event = threading.Event()
        statuses: list[str] = []
        app = types.SimpleNamespace(
            closing=False,
            voice_conversation_active=True,
            voice_conversation_listening=True,
            voice_conversation_stop_event=stop_event,
            voice_conversation_retry_attempt=0,
            voice_conversation_retry_timer=FakeTimer(),
            galaxy_transition=types.SimpleNamespace(
                set_conversation_status=statuses.append
            ),
            status_label=types.SimpleNamespace(setText=statuses.append),
        )

        Quasar.QuasarDesktopApp._finish_voice_conversation_error(
            app,
            "마이크 연결이 끊겼습니다",
        )

        self.assertTrue(app.voice_conversation_active)
        self.assertFalse(app.voice_conversation_listening)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(app.voice_conversation_retry_attempt, 1)
        self.assertEqual(app.voice_conversation_retry_timer.delay, 1_000)
        self.assertEqual(statuses[0], statuses[1])
        self.assertIn("1초 후 다시 듣습니다", statuses[0])

    def test_tts_tool_changes_setting_and_delegates_other_tools(self) -> None:
        emitted: list[bool] = []
        original_run_tool = Mock(return_value="delegated")
        app = types.SimpleNamespace(
            signals=types.SimpleNamespace(
                tts_enabled_requested=types.SimpleNamespace(emit=emitted.append)
            ),
            original_run_tool=original_run_tool,
        )

        result = Quasar.QuasarDesktopApp._run_tool(
            app,
            "set_tts_enabled",
            {"enabled": False},
        )
        self.assertEqual(result, "Quasar spoken responses disabled.")
        self.assertEqual(emitted, [False])
        self.assertEqual(
            Quasar.QuasarDesktopApp._run_tool(
                app,
                "get_current_time",
                {},
            ),
            "delegated",
        )
        original_run_tool.assert_called_once_with(
            "get_current_time",
            {},
            None,
            None,
        )
        with self.assertRaises(ValueError):
            Quasar.QuasarDesktopApp._run_tool(
                app,
                "set_tts_enabled",
                {"enabled": "off"},
            )

    def test_disabling_tts_persists_setting_and_stops_current_speech(self) -> None:
        class FakeToggle:
            def __init__(self) -> None:
                self.checked = True

            def isChecked(self) -> bool:
                return self.checked

            def setChecked(self, checked: bool) -> None:
                self.checked = checked

        settings = Mock()
        stop_speech = Mock()
        toggle = FakeToggle()

        Quasar.QuasarDesktopApp._set_tts_enabled(
            types.SimpleNamespace(
                voice_response_toggle=toggle,
                settings=settings,
                speech_worker=object(),
                _stop_speech=stop_speech,
            ),
            False,
        )

        self.assertFalse(toggle.checked)
        settings.setValue.assert_called_once_with("voice/tts_enabled", False)
        settings.sync.assert_called_once_with()
        stop_speech.assert_called_once_with()

    def test_request_stop_sets_cancel_event_and_closes_request_client(self) -> None:
        cancel_event = threading.Event()
        request_client = Mock()
        app = types.SimpleNamespace(
            busy=True,
            request_cancel_event=cancel_event,
            request_cancel_pending=False,
            request_client=request_client,
            send_button=Mock(),
            status_label=Mock(),
            activity_body=types.SimpleNamespace(setText=Mock()),
        )

        Quasar.QuasarDesktopApp._cancel_request(app)

        self.assertTrue(cancel_event.is_set())
        self.assertTrue(app.request_cancel_pending)
        request_client.close.assert_called_once_with()
        app.send_button.setText.assert_called_once_with("중지 중...")

    def test_request_cancellation_does_not_add_a_late_answer_to_history(self) -> None:
        cancel_event = threading.Event()
        client = Mock()

        def finish_request(**_kwargs: object) -> types.SimpleNamespace:
            cancel_event.set()
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            tool_calls=None,
                            content="Late answer",
                        )
                    )
                ]
            )

        client.chat.completions.create.side_effect = finish_request
        messages: list[dict[str, str]] = []

        with self.assertRaises(InterruptedError):
            Quasar.agent.request_agent(
                client,
                "gpt-4o",
                messages,
                "test request",
                cancel_event=cancel_event,
            )

        self.assertEqual(messages, [])

    def test_stopping_speech_in_voice_conversation_starts_listening(self) -> None:
        speech_worker = Mock()
        start_capture = Mock()
        app = types.SimpleNamespace(
            speech_worker=speech_worker,
            busy=False,
            voice_conversation_active=True,
            send_button=Mock(),
            status_label=Mock(),
            galaxy_transition=types.SimpleNamespace(
                set_conversation_status=Mock()
            ),
            _start_voice_conversation_capture=start_capture,
        )

        Quasar.QuasarDesktopApp._stop_speech(app)

        self.assertIsNone(app.speech_worker)
        speech_worker.stop.assert_called_once_with()
        start_capture.assert_called_once_with()
        app.send_button.setText.assert_called_once_with("전송")

    def test_ctrl_space_interrupts_tts_in_voice_conversation(self) -> None:
        stop_speech = Mock()
        app = types.SimpleNamespace(
            closing=False,
            voice_conversation_active=True,
            speech_worker=object(),
            _stop_speech=stop_speech,
        )

        Quasar.QuasarDesktopApp._interrupt_voice_conversation(app)

        stop_speech.assert_called_once_with()

    def test_voice_conversation_skips_tts_when_disabled(self) -> None:
        class FakeToggle:
            def isChecked(self) -> bool:
                return False

        app = types.SimpleNamespace(
            closing=False,
            activity_body=object(),
            messages=[{"role": "system", "content": "system"}],
            voice_response_toggle=FakeToggle(),
            voice_conversation_active=True,
            _clear_request_state=Mock(),
            _add_message=Mock(),
            _set_busy=Mock(),
            _speak_response=Mock(return_value=True),
            _start_voice_conversation_capture=Mock(),
        )

        Quasar.QuasarDesktopApp._finish_with_response(
            app,
            "답변입니다.",
            [{"role": "system", "content": "system"}],
        )

        app._speak_response.assert_not_called()
        app._start_voice_conversation_capture.assert_called_once_with()

    def test_speech_output_uses_edge_neural_voices(self) -> None:
        requested: list[tuple[str, str]] = []
        completed: list[bool] = []
        errors: list[str] = []
        playback: dict[str, object] = {}
        signals = Quasar.AppSignals()
        signals.speech_finished.connect(lambda _worker: completed.append(True))
        signals.speech_failed.connect(lambda _worker, error: errors.append(error))

        def fake_run(command: list[str], **_kwargs: object) -> types.SimpleNamespace:
            text_path = Path(command[command.index("--file") + 1])
            audio_path = Path(command[command.index("--write-media") + 1])
            voice = command[command.index("--voice") + 1]
            requested.append((text_path.read_text(encoding="utf-8"), voice))
            audio_path.write_bytes(b"fake mp3")
            return types.SimpleNamespace(returncode=0, stderr="", stdout="")

        class FakeFrame:
            def to_ndarray(self) -> np.ndarray:
                return np.array([[0.01, 0.5, -0.01]], dtype=np.float32)

        class FakeStream:
            rate = 24_000

        class FakeContainer:
            streams = types.SimpleNamespace(audio=[FakeStream()])

            def __enter__(self) -> "FakeContainer":
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

            def decode(self, _stream: object) -> list[FakeFrame]:
                return [FakeFrame()]

        av = types.ModuleType("av")
        av.open = lambda _filename: FakeContainer()
        av.AudioResampler = lambda **_kwargs: types.SimpleNamespace(
            resample=lambda frame: [] if frame is None else [frame]
        )
        sounddevice = types.ModuleType("sounddevice")

        def play(samples: np.ndarray, sample_rate: int) -> None:
            playback["samples"] = samples.copy()
            playback["sample_rate"] = sample_rate

        sounddevice.play = play
        sounddevice.get_stream = lambda: types.SimpleNamespace(active=False)
        sounddevice.stop = lambda: None

        with patch.dict(
            sys.modules,
            {"av": av, "sounddevice": sounddevice},
        ), patch(
            "Quasar.subprocess.run",
            side_effect=fake_run,
        ):
            Quasar.LocalSpeechOutputWorker("음성 출력 테스트", signals).run()
            Quasar.LocalSpeechOutputWorker("English voice test", signals).run()

        self.assertEqual(
            requested,
            [
                ("음성 출력 테스트", "ko-KR-SunHiNeural"),
                ("English voice test", "en-US-AriaNeural"),
            ],
        )
        self.assertEqual(errors, [])
        self.assertEqual(completed, [True, True])
        self.assertEqual(playback["sample_rate"], 24_000)
        played_samples = playback["samples"]
        self.assertIsInstance(played_samples, np.ndarray)
        self.assertAlmostEqual(
            float(played_samples[0]),
            np.tanh(0.01 * Quasar.SPEECH_OUTPUT_GAIN)
            / np.tanh(Quasar.SPEECH_OUTPUT_GAIN),
            places=6,
        )
        self.assertLessEqual(float(np.max(np.abs(played_samples))), 1.0)


if __name__ == "__main__":
    unittest.main()
