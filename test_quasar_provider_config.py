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


if __name__ == "__main__":
    unittest.main()
