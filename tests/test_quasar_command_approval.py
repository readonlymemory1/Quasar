import unittest
from unittest.mock import patch

import Quasar
import quasar_cli as agent


class CommandExecutionTests(unittest.TestCase):
    def test_high_risk_commands_require_gui_approval(self) -> None:
        original_run_tool = agent.run_tool
        previous_auto_approve = agent.auto_approve_commands
        previous_approval_callback = agent.tool_approval_callback
        executed_commands: list[str] = []
        approved_actions: list[str] = []

        class Harness:
            def _request_tool_approval(self, name, _arguments):
                approved_actions.append(name)
                return True

        Harness.original_run_tool = staticmethod(original_run_tool)
        Harness._run_tool = staticmethod(
            lambda name, arguments, live_analysis_server=None, mcp_manager=None:
            original_run_tool(
                name,
                arguments,
                live_analysis_server,
                mcp_manager,
            )
        )

        try:
            Quasar.QuasarDesktopApp._configure_agent(Harness())
            self.assertTrue(agent.auto_approve_commands)
            with patch.object(
                agent,
                "execute_cmd",
                side_effect=lambda command: executed_commands.append(command)
                or "executed",
            ), patch.object(
                agent,
                "schedule_command",
                return_value="scheduled",
            ), patch(
                "builtins.input",
                side_effect=AssertionError("GUI approval must not use console input"),
            ):
                self.assertEqual(
                    agent.run_tool(
                        "execute_cmd",
                        {"command": "Remove-Item -Recurse C:\\temp\\data"},
                    ),
                    "executed",
                )
                self.assertEqual(
                    agent.run_tool(
                        "schedule_command",
                        {
                            "command": "shutdown /s",
                            "target_datetime": "2026-11-03 12:00",
                        },
                    ),
                    "scheduled",
                )
        finally:
            agent.run_tool = original_run_tool
            agent.auto_approve_commands = previous_auto_approve
            agent.tool_approval_callback = previous_approval_callback

        self.assertEqual(
            executed_commands,
            ["Remove-Item -Recurse C:\\temp\\data"],
        )
        self.assertEqual(approved_actions, ["execute_cmd", "schedule_command"])


if __name__ == "__main__":
    unittest.main()
