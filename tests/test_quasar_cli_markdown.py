import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

import quasar_cli


class QuasarCliMarkdownTests(unittest.TestCase):
    def test_response_is_rendered_as_markdown(self) -> None:
        output = io.StringIO()
        test_console = Console(file=output, force_terminal=False, width=80)
        with patch.object(quasar_cli, "console", test_console):
            quasar_cli.print_markdown_response(
                "# 제목\n\n**강조**와 `코드`\n\n```python\nprint('완료')\n```"
            )

        rendered = output.getvalue()
        self.assertIn("Quasar:", rendered)
        self.assertIn("제목", rendered)
        self.assertIn("강조", rendered)
        self.assertIn("코드", rendered)
        self.assertIn("print('완료')", rendered)
        self.assertNotIn("**강조**", rendered)
        self.assertNotIn("```python", rendered)

    def test_request_reports_live_tool_progress(self) -> None:
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="todo_list", arguments="{}"),
        )
        tool_message = SimpleNamespace(
            content=None,
            tool_calls=[tool_call],
            model_dump=lambda **_kwargs: {"role": "assistant", "tool_calls": []},
        )
        answer_message = SimpleNamespace(content="할 일 목록입니다.", tool_calls=[])
        responses = iter(
            [
                SimpleNamespace(choices=[SimpleNamespace(message=tool_message)]),
                SimpleNamespace(choices=[SimpleNamespace(message=answer_message)]),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: next(responses))
            )
        )
        progress: list[str] = []
        messages: list[dict[str, str]] = []

        with patch.object(quasar_cli, "is_responses_model", return_value=False), patch.object(
            quasar_cli, "run_tool", return_value="[]"
        ), patch("sys.stdout", new=io.StringIO()):
            answer = quasar_cli.request_agent(
                client,
                "test-model",
                messages,
                "내 할 일 보여줘",
                progress_callback=progress.append,
            )

        self.assertEqual(answer, "할 일 목록입니다.")
        self.assertEqual(
            progress,
            [
                "요청을 분석하고 있습니다...",
                "도구 실행 중: todo_list",
                "도구 완료: todo_list",
                "요청을 분석하고 있습니다...",
            ],
        )


if __name__ == "__main__":
    unittest.main()
