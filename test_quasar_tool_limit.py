import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import quasar_cli


class ToolRoundLimitTests(unittest.TestCase):
    def test_limit_defaults_to_eight_and_reads_environment_override(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(quasar_cli.get_max_tool_rounds(), 8)
        with patch.dict(os.environ, {"QUASAR_MAX_TOOL_ROUNDS": "3"}):
            self.assertEqual(quasar_cli.get_max_tool_rounds(), 3)

    def test_limit_rejects_values_outside_supported_range(self) -> None:
        for value in (0, 33, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quasar_cli.get_max_tool_rounds(value)
        with patch.dict(os.environ, {"QUASAR_MAX_TOOL_ROUNDS": "many"}):
            with self.assertRaisesRegex(ValueError, "QUASAR_MAX_TOOL_ROUNDS"):
                quasar_cli.get_max_tool_rounds()

    def test_chat_api_obeys_configured_tool_round_limit(self) -> None:
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="todo_list", arguments="{}"),
        )
        tool_message = SimpleNamespace(
            content=None,
            tool_calls=[tool_call],
            model_dump=lambda **_kwargs: {"role": "assistant", "tool_calls": []},
        )
        answer_message = SimpleNamespace(content="완료", tool_calls=[])
        responses = iter(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(message=tool_message)]
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(message=answer_message)]
                ),
            ]
        )
        create = Mock(side_effect=lambda **_kwargs: next(responses))
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )

        with (
            patch.object(quasar_cli, "is_responses_model", return_value=False),
            patch.object(quasar_cli, "run_tool", return_value="[]") as run_tool,
            patch("sys.stdout"),
        ):
            answer = quasar_cli.request_agent(
                client,
                "test-model",
                [],
                "할 일 보여줘",
                max_tool_rounds=1,
            )

        self.assertEqual(answer, "완료")
        self.assertEqual(create.call_count, 2)
        run_tool.assert_called_once()

    def test_responses_api_obeys_configured_tool_round_limit(self) -> None:
        function_call = SimpleNamespace(
            type="function_call",
            call_id="call-1",
            name="todo_list",
            arguments="{}",
            model_dump=lambda **_kwargs: {
                "type": "function_call",
                "call_id": "call-1",
                "name": "todo_list",
                "arguments": "{}",
            },
        )
        tool_response = SimpleNamespace(
            output=[function_call],
            output_text="",
        )
        answer_response = SimpleNamespace(output=[], output_text="완료")
        create = Mock(side_effect=[tool_response, answer_response])
        client = SimpleNamespace(
            responses=SimpleNamespace(create=create)
        )

        with (
            patch.object(quasar_cli, "is_responses_model", return_value=True),
            patch.object(quasar_cli, "to_responses_input", return_value=[]),
            patch.object(quasar_cli, "run_tool", return_value="[]") as run_tool,
            patch("sys.stdout"),
        ):
            answer = quasar_cli.request_agent(
                client,
                "test-responses-model",
                [],
                "할 일 보여줘",
                max_tool_rounds=1,
            )

        self.assertEqual(answer, "완료")
        self.assertEqual(create.call_count, 2)
        run_tool.assert_called_once()


if __name__ == "__main__":
    unittest.main()
