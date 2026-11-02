import unittest
from unittest.mock import patch

import Quasar
import quasar_cli as agent


class CommandExecutionTests(unittest.TestCase):
    def test_commands_run_without_confirmation_dialogs(self) -> None:
        original_run_tool = agent.run_tool
        previous_auto_approve = agent.auto_approve_commands
        executed_commands: list[str] = []

        class Harness:
            pass

        Harness.original_run_tool = staticmethod(original_run_tool)

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
                side_effect=AssertionError("Command execution must not prompt"),
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

        self.assertEqual(
            executed_commands,
            ["Remove-Item -Recurse C:\\temp\\data"],
        )


if __name__ == "__main__":
    unittest.main()
