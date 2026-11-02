import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

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
        self.assertTrue(0.11 < float(np.max(captured[0])) < 0.13)
        self.assertEqual(errors, [])

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

    def test_default_nemotron_model_transcribes_korean(self) -> None:
        recognized: list[str] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.voice_text_ready.connect(recognized.append)
        signals.voice_failed.connect(errors.append)
        calls: dict[str, object] = {}

        def fake_run(command: list[str], **kwargs: object) -> types.SimpleNamespace:
            calls["command"] = command
            calls["options"] = kwargs
            with wave.open(command[2], "rb") as wav_file:
                calls["wav_format"] = (
                    wav_file.getnchannels(),
                    wav_file.getsampwidth(),
                    wav_file.getframerate(),
                    wav_file.getnframes(),
                )
            return types.SimpleNamespace(
                returncode=0,
                stdout=" 안녕하세요\n",
                stderr="",
            )

        recognizer = Quasar.LocalSpeechRecognizer()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            recognizer,
            "_prepare_runtime_paths",
            return_value=({}, Path(temp_dir)),
        ), patch("Quasar.shutil.which", return_value="nemo-speech"), patch(
            "Quasar.subprocess.run",
            side_effect=fake_run,
        ):
            recognizer.transcribe(np.full(16_000, 0.5, dtype=np.float32), signals)

        self.assertEqual(recognized, ["안녕하세요"])
        self.assertEqual(errors, [])
        self.assertIn(Quasar.NEMO_SPEECH_MODEL, calls["command"])
        self.assertIn("ko-KR", calls["command"])
        self.assertEqual(calls["wav_format"], (1, 2, 16_000, 16_000))
        self.assertEqual(calls["options"]["timeout"], 900)

    def test_local_speech_output_uses_windows_sapi(self) -> None:
        spoken: list[str] = []
        completed: list[bool] = []
        errors: list[str] = []
        signals = Quasar.AppSignals()
        signals.speech_finished.connect(lambda: completed.append(True))
        signals.speech_failed.connect(errors.append)

        class FakeVoice:
            name = "Microsoft Heami Desktop"
            id = "korean-voice"

        class FakeEngine:
            def getProperty(self, name: str) -> list[FakeVoice]:
                self.property_name = name
                return [FakeVoice()]

            def setProperty(self, name: str, value: str) -> None:
                self.selected_voice = (name, value)

            def say(self, text: str) -> None:
                spoken.append(text)

            def runAndWait(self) -> None:
                return None

            def stop(self) -> None:
                return None

        engine = FakeEngine()
        pyttsx3 = types.ModuleType("pyttsx3")
        pyttsx3.init = lambda driver: engine

        with patch.dict(sys.modules, {"pyttsx3": pyttsx3}):
            Quasar.LocalSpeechOutputWorker("음성 출력 테스트", signals).run()

        self.assertEqual(spoken, ["음성 출력 테스트"])
        self.assertEqual(engine.selected_voice, ("voice", "korean-voice"))
        self.assertEqual(completed, [True])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
