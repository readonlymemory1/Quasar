import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import quasar_cli
from Quasar import ModelLoader


class NvidiaNimProviderTests(unittest.TestCase):
    def test_create_client_uses_nvidia_nim_endpoint_and_default_model(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "NVIDIA_API_KEY": "test-key",
                    "NVIDIA_NIM_BASE_URL": "https://nim.example/v1",
                },
                clear=True,
            ),
            patch("quasar_cli.OpenAI") as openai,
        ):
            client, model = quasar_cli.create_client("nvidia_nim")

        self.assertIs(client, openai.return_value)
        self.assertEqual(model, "nvidia/llama-3.1-nemotron-70b-instruct")
        openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://nim.example/v1",
        )

    def test_create_client_honors_nvidia_nim_model_override(self) -> None:
        with (
            patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"}, clear=True),
            patch("quasar_cli.OpenAI"),
        ):
            _client, model = quasar_cli.create_client(
                "nvidia_nim",
                "custom/model",
            )

        self.assertEqual(model, "custom/model")

    def test_create_client_reads_nvidia_nim_model_from_environment(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "NVIDIA_API_KEY": "test-key",
                    "NVIDIA_NIM_MODEL": "meta/custom-model",
                },
                clear=True,
            ),
            patch("quasar_cli.OpenAI"),
        ):
            _client, model = quasar_cli.create_client("nvidia_nim")

        self.assertEqual(model, "meta/custom-model")

    def test_create_client_requires_nvidia_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "NVIDIA_API_KEY"):
                quasar_cli.create_client("nvidia_nim")

    def test_desktop_model_loader_keeps_all_nvidia_nim_model_ids(self) -> None:
        loaded: list[tuple[str, set[str], list[str]]] = []
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=Mock(
                    return_value=SimpleNamespace(
                        data=[
                            SimpleNamespace(id="meta/llama-3.3-70b-instruct"),
                            SimpleNamespace(id="nvidia/nemotron"),
                        ]
                    )
                )
            )
        )

        ModelLoader("nvidia_nim", client, lambda *args: loaded.append(args)).run()

        self.assertEqual(
            loaded,
            [
                (
                    "nvidia_nim",
                    {"meta/llama-3.3-70b-instruct", "nvidia/nemotron"},
                    [],
                )
            ],
        )

    def test_nvidia_nim_model_picker_keeps_all_listed_models(self) -> None:
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=Mock(
                    return_value=SimpleNamespace(
                        data=[
                            SimpleNamespace(id="meta/llama-3.3-70b-instruct"),
                            SimpleNamespace(id="nvidia/nemotron"),
                        ]
                    )
                )
            )
        )
        with patch("builtins.input", return_value="2"):
            selected = quasar_cli.choose_model(
                client,
                "nvidia_nim",
                "nvidia/default-model",
            )

        self.assertEqual(selected, "meta/llama-3.3-70b-instruct")


class OmniProviderTests(unittest.TestCase):
    def test_create_client_uses_omni_endpoint_and_default_model(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"OMNI_API_KEY": "test-key"},
                clear=True,
            ),
            patch("quasar_cli.OpenAI") as openai,
        ):
            client, model = quasar_cli.create_client("omni")

        self.assertIs(client, openai.return_value)
        self.assertEqual(model, "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://integrate.api.nvidia.com/v1",
        )

    def test_create_client_reuses_nvidia_api_key_for_omni(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"NVIDIA_API_KEY": "nvidia-key"},
                clear=True,
            ),
            patch("quasar_cli.OpenAI") as openai,
        ):
            client, model = quasar_cli.create_client("omni")

        self.assertIs(client, openai.return_value)
        openai.assert_called_once_with(
            api_key="nvidia-key",
            base_url="https://integrate.api.nvidia.com/v1",
        )

    def test_create_client_prefers_omni_key_over_nvidia_key(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "OMNI_API_KEY": "omni-key",
                    "NVIDIA_API_KEY": "nvidia-key",
                },
                clear=True,
            ),
            patch("quasar_cli.OpenAI") as openai,
        ):
            _client, _model = quasar_cli.create_client("omni")

        openai.assert_called_once_with(
            api_key="omni-key",
            base_url="https://integrate.api.nvidia.com/v1",
        )

    def test_create_client_honors_omni_model_override(self) -> None:
        with (
            patch.dict(os.environ, {"OMNI_API_KEY": "test-key"}, clear=True),
            patch("quasar_cli.OpenAI"),
        ):
            _client, model = quasar_cli.create_client(
                "omni",
                "custom/omni-model",
            )

        self.assertEqual(model, "custom/omni-model")

    def test_create_client_reads_omni_model_from_environment(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "OMNI_API_KEY": "test-key",
                    "OMNI_MODEL": "prod/omni-large",
                },
                clear=True,
            ),
            patch("quasar_cli.OpenAI"),
        ):
            _client, model = quasar_cli.create_client("omni")

        self.assertEqual(model, "prod/omni-large")

    def test_create_client_requires_omni_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OMNI_API_KEY"):
                quasar_cli.create_client("omni")

    def test_request_agent_attaches_input_audio_to_user_message(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(tool_calls=None, content="들었어요"),
                ),
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(return_value=response),
                ),
            ),
        )

        answer = quasar_cli.request_agent(
            client,
            "qwen3-omni",
            [],
            "(음성 질문)",
            audio_b64="QUJD",
            tool_calling_mode="legacy",
        )

        self.assertEqual(answer, "들었어요")
        _args, kwargs = client.chat.completions.create.call_args
        sent_messages = kwargs["messages"]
        self.assertEqual(len(sent_messages), 1)
        content = sent_messages[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "(음성 질문)"})
        self.assertEqual(
            content[1],
            {
                "type": "audio_url",
                "audio_url": {"url": "data:audio/wav;base64,QUJD"},
            },
        )

    def test_request_agent_can_send_openai_input_audio_style(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(tool_calls=None, content="ok"),
                ),
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(return_value=response),
                ),
            ),
        )

        with patch.dict(os.environ, {"OMNI_AUDIO_STYLE": "input_audio"}, clear=True):
            quasar_cli.request_agent(
                client,
                "qwen3-omni",
                [],
                "(음성 질문)",
                audio_b64="QUJD",
                tool_calling_mode="legacy",
            )

        _args, kwargs = client.chat.completions.create.call_args
        content = kwargs["messages"][0]["content"]
        self.assertEqual(
            content[1],
            {
                "type": "input_audio",
                "input_audio": {"data": "QUJD", "format": "wav"},
            },
        )


    def test_request_agent_keeps_audio_in_history_for_next_turn(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(tool_calls=None, content="들었어요"),
                ),
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(return_value=response),
                ),
            ),
        )
        messages: list[dict[str, object]] = []

        quasar_cli.request_agent(
            client,
            "qwen3-omni",
            messages,
            "(음성 질문)",
            audio_b64="QUJD",
            tool_calling_mode="legacy",
        )

        # 요청이 끝난 뒤에도 기록에 오디오 파트가 남아 있어야 다음 요청에서
        # 모델이 바로 전 음성을 다시 들을 수 있다.
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        content = messages[0]["content"]
        assert isinstance(content, list)
        self.assertEqual(
            content,
            [
                {"type": "text", "text": "(음성 질문)"},
                {
                    "type": "audio_url",
                    "audio_url": {"url": "data:audio/wav;base64,QUJD"},
                },
            ],
        )


class OmniVoiceTests(unittest.TestCase):
    def test_model_loader_keeps_all_omni_model_ids(self) -> None:
        loaded: list[tuple[str, set[str], list[str]]] = []
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=Mock(
                    return_value=SimpleNamespace(
                        data=[
                            SimpleNamespace(id="qwen3-omni"),
                            SimpleNamespace(id="omni-small"),
                        ]
                    )
                )
            )
        )

        ModelLoader("omni", client, lambda *args: loaded.append(args)).run()

        self.assertEqual(
            loaded,
            [
                (
                    "omni",
                    {"qwen3-omni", "omni-small"},
                    [],
                )
            ],
        )

    def test_omni_speech_recognizer_emits_wav_audio_and_placeholder(self) -> None:
        import base64

        import numpy as np

        from Quasar import (
            AppSignals,
            OmniSpeechRecognizer,
            VOICE_OMNI_PLACEHOLDER,
            VOICE_RECOGNITION_SAMPLE_RATE,
        )

        signals = AppSignals()
        audio_chunks: list[str] = []
        transcripts: list[str] = []
        signals.voice_audio_ready.connect(audio_chunks.append)
        signals.voice_text_ready.connect(transcripts.append)

        OmniSpeechRecognizer().transcribe(
            np.full(VOICE_RECOGNITION_SAMPLE_RATE * 2, 0.1, dtype=np.float32),
            signals,
        )

        self.assertEqual(transcripts, [VOICE_OMNI_PLACEHOLDER])
        self.assertEqual(len(audio_chunks), 1)
        wav_bytes = base64.b64decode(audio_chunks[0])
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        # 2초짜리 16kHz 모노 16비트 WAV는 헤더를 더해 대략 64KB여야 한다.
        self.assertAlmostEqual(
            len(wav_bytes),
            VOICE_RECOGNITION_SAMPLE_RATE * 2 * 2 + 44,
            delta=128,
        )


if __name__ == "__main__":
    unittest.main()
