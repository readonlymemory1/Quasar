import contextlib
import io
import unittest
from unittest.mock import patch

import quasar_cli


class QuasarDebugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_debug_mode = quasar_cli.debug_mode
        quasar_cli.debug_mode = True

    def tearDown(self) -> None:
        quasar_cli.debug_mode = self.previous_debug_mode

    def test_debug_prints_tool_arguments_and_redacts_secrets(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            quasar_cli._debug_tool_call(
                "execute_cmd",
                {
                    "command": (
                        "python app.py OPENAI_API_KEY=TEST-KEY-VALUE "
                        "--password command-pass Bearer bearer-secret"
                    ),
                    "api_token": "private-token",
                    "body": "private email",
                },
            )
        rendered = output.getvalue()
        self.assertIn("execute_cmd", rendered)
        self.assertIn("python app.py", rendered)
        self.assertNotIn("TEST-KEY-VALUE", rendered)
        self.assertNotIn("private-token", rendered)
        self.assertNotIn("private email", rendered)
        self.assertNotIn("command-pass", rendered)
        self.assertNotIn("bearer-secret", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_sensitive_tool_results_are_summarized(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            quasar_cli._debug_tool_result("read_workspace_file", "private file content")
        rendered = output.getvalue()
        self.assertNotIn("private file content", rendered)
        self.assertIn("결과 내용 숨김", rendered)

    def test_debug_command_toggles_mode(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            quasar_cli.handle_cli_command("/debug off", [])
        self.assertFalse(quasar_cli.debug_mode)
        self.assertIn("꺼짐", output.getvalue())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            quasar_cli.handle_cli_command("/debug on", [])
        self.assertTrue(quasar_cli.debug_mode)
        self.assertIn("켜짐", output.getvalue())

    def test_debug_output_is_disabled_by_default(self) -> None:
        with patch.object(quasar_cli, "debug_mode", False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                quasar_cli._debug_tool_call("execute_cmd", {"command": "python app.py"})
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
