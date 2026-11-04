import os
import subprocess
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import quasar_cli
import quasar_tool_calling


def chat_tool_call(name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def chat_tool_response(*tool_calls: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=list(tool_calls),
                    model_dump=lambda **_kwargs: {
                        "role": "assistant",
                        "tool_calls": [],
                    },
                )
            )
        ]
    )


class ToolCallingModeTests(unittest.TestCase):
    def test_mode_defaults_to_dynamic_and_reads_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(quasar_cli.get_tool_calling_mode(), "dynamic")
        with patch.dict(os.environ, {"QUASAR_TOOL_CALLING_MODE": "dynamic"}):
            self.assertEqual(quasar_cli.get_tool_calling_mode(), "dynamic")
        with patch.dict(os.environ, {"QUASAR_TOOL_CALLING_MODE": "legacy"}):
            self.assertEqual(quasar_cli.get_tool_calling_mode(), "legacy")

    def test_mode_resolver_defaults_to_dynamic(self) -> None:
        self.assertEqual(quasar_tool_calling.resolve_tool_calling_mode(), "dynamic")

    def test_mode_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy.*dynamic"):
            quasar_cli.get_tool_calling_mode("automatic")

    def test_category_selection_loads_multiple_categories_and_keeps_them(self) -> None:
        loaded_categories: set[str] = set()
        categories_result = quasar_tool_calling.load_categories(
            ["code", "files"],
            loaded_categories,
            quasar_cli.TOOLS,
        )
        self.assertIn("코드", categories_result)
        self.assertIn("파일", categories_result)
        available = quasar_tool_calling.tools_for_categories(
            quasar_cli.TOOLS,
            loaded_categories,
        )
        names = {tool["function"]["name"] for tool in available}
        self.assertTrue({"python_check_syntax", "read_workspace_file"} <= names)
        self.assertNotIn("web_search", names)

        quasar_tool_calling.load_categories(
            ["web"],
            loaded_categories,
            quasar_cli.TOOLS,
        )
        names = {
            tool["function"]["name"]
            for tool in quasar_tool_calling.tools_for_categories(
                quasar_cli.TOOLS,
                loaded_categories,
            )
        }
        self.assertTrue({"python_check_syntax", "read_workspace_file", "web_search"} <= names)

    def test_invalid_category_does_not_change_loaded_state(self) -> None:
        loaded_categories: set[str] = set()
        result = quasar_tool_calling.load_categories(
            ["code", "unknown"],
            loaded_categories,
            quasar_cli.TOOLS,
        )
        self.assertIn("알 수 없는 카테고리", result)
        self.assertEqual(loaded_categories, set())

    def test_initial_dynamic_tools_only_include_category_selector_and_controls(self) -> None:
        available = quasar_cli._available_tool_schemas(
            "dynamic",
            set(),
            set(),
            None,
        )
        names = {tool["function"]["name"] for tool in available}
        self.assertEqual(
            names,
            {"select_tool_categories", "message_to_user", "end"},
        )
        self.assertFalse(names & {
            "python_check_syntax",
            "web_search",
            "gmail_search",
            "discover_mcp_tools",
        })

        selector = next(
            tool for tool in available
            if tool["function"]["name"] == "select_tool_categories"
        )["function"]
        description = selector["description"]
        self.assertIn("code:", description)
        self.assertIn("web:", description)
        self.assertIn("주요 기능", description)
        categories = selector["parameters"]["properties"]["categories"]
        self.assertEqual(categories["type"], "array")
        self.assertEqual(
            categories["items"]["enum"],
            list(quasar_tool_calling.CATEGORY_DETAILS),
        )

    def test_legacy_mode_keeps_existing_full_tool_list(self) -> None:
        available = quasar_cli._available_tool_schemas(
            "legacy",
            set(),
            set(),
            None,
        )
        available_names = {tool["function"]["name"] for tool in available}
        expected_names = {
            tool["function"]["name"]
            for tool in quasar_cli.TOOLS
            if tool["function"]["name"] != "discover_mcp_tools"
        }
        self.assertEqual(available_names, expected_names)
        self.assertNotIn("load_tool_categories", available_names)

    def test_chat_api_starts_with_category_selector_then_loads_selected_categories(self) -> None:
        load_call = chat_tool_call(
            "select_tool_categories",
            '{"categories":["code","files"]}',
        )
        run_call = chat_tool_call("python_check_syntax", '{"file_path":"sample.py"}')
        responses = iter([
            chat_tool_response(load_call),
            chat_tool_response(run_call),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="완료", tool_calls=[])
                    )
                ]
            ),
        ])
        create = Mock(side_effect=lambda **_kwargs: next(responses))
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )

        with (
            patch.object(quasar_cli, "is_responses_model", return_value=False),
            patch.object(quasar_cli, "run_tool", return_value="syntax ok") as run_tool,
            patch("sys.stdout"),
        ):
            answer = quasar_cli.request_agent(
                client,
                "test-model",
                [],
                "sample.py 문법 검사",
                tool_calling_mode="dynamic",
            )

        self.assertEqual(answer, "완료")
        run_tool.assert_called_once()
        calls = create.call_args_list
        initial_names = {
            tool["function"]["name"] for tool in calls[0].kwargs["tools"]
        }
        selected_names = {
            tool["function"]["name"] for tool in calls[1].kwargs["tools"]
        }
        self.assertEqual(
            initial_names,
            {
                "select_tool_categories",
                "message_to_user",
                "end",
            },
        )
        self.assertIn("python_check_syntax", selected_names)
        self.assertIn("read_workspace_file", selected_names)
        self.assertNotIn("web_search", selected_names)
        selector = next(
            tool
            for tool in calls[0].kwargs["tools"]
            if tool["function"]["name"] == "select_tool_categories"
        )
        self.assertTrue(
            selector["function"]["description"].startswith(
                "요청을 수행하는 데 필요한 도구 카테고리를 하나 이상 선택합니다."
            )
        )

    def test_responses_api_uses_dynamic_tool_schemas(self) -> None:
        load_call = SimpleNamespace(
            type="function_call",
            call_id="call-select",
            name="select_tool_categories",
            arguments='{"categories":["calendar","tasks"]}',
            model_dump=lambda **_kwargs: {
                "type": "function_call",
                "call_id": "call-select",
                "name": "select_tool_categories",
                "arguments": '{"categories":["calendar","tasks"]}',
            },
        )
        final_response = SimpleNamespace(output=[], output_text="완료")
        create = Mock(side_effect=[
            SimpleNamespace(output=[load_call], output_text=""),
            final_response,
        ])
        client = SimpleNamespace(responses=SimpleNamespace(create=create))

        with (
            patch.object(quasar_cli, "is_responses_model", return_value=True),
            patch.object(quasar_cli, "to_responses_input", return_value=[]),
            patch("sys.stdout"),
        ):
            answer = quasar_cli.request_agent(
                client,
                "test-responses-model",
                [],
                "지금 시각 확인",
                tool_calling_mode="dynamic",
            )

        self.assertEqual(answer, "완료")
        initial_names = {tool["name"] for tool in create.call_args_list[0].kwargs["tools"]}
        loaded_names = {tool["name"] for tool in create.call_args_list[1].kwargs["tools"]}
        self.assertEqual(
            initial_names,
            {"select_tool_categories", "message_to_user", "end"},
        )
        self.assertIn("get_current_time", loaded_names)
        self.assertIn("todo_list", loaded_names)
        self.assertNotIn("web_search", loaded_names)


class HighRiskCommandApprovalTests(unittest.TestCase):
    def test_high_risk_command_requires_approval_even_when_auto_approve_is_enabled(self) -> None:
        with (
            patch.object(quasar_cli, "auto_approve_commands", True),
            patch.object(quasar_cli, "tool_approval_callback", return_value=False),
            patch.object(quasar_cli, "execute_cmd") as execute_cmd,
        ):
            result = quasar_cli.run_tool(
                "execute_cmd",
                {"command": "Remove-Item -Recurse C:\\temp\\data"},
            )

        self.assertIn("승인되지 않았습니다", result)
        execute_cmd.assert_not_called()

    def test_approved_high_risk_command_executes(self) -> None:
        with (
            patch.object(quasar_cli, "auto_approve_commands", True),
            patch.object(quasar_cli, "tool_approval_callback", return_value=True),
            patch.object(quasar_cli, "execute_cmd", return_value="done") as execute_cmd,
        ):
            result = quasar_cli.run_tool(
                "execute_cmd",
                {"command": "Remove-Item -Recurse C:\\temp\\data"},
            )

        self.assertEqual(result, "done")
        execute_cmd.assert_called_once()

    def test_non_destructive_command_does_not_require_risk_approval(self) -> None:
        with (
            patch.object(quasar_cli, "auto_approve_commands", True),
            patch.object(quasar_cli, "tool_approval_callback", side_effect=AssertionError),
            patch.object(quasar_cli, "execute_cmd", return_value="done"),
        ):
            self.assertEqual(
                quasar_cli.run_tool("execute_cmd", {"command": "python --version"}),
                "done",
            )

    def test_command_waits_and_returns_complete_output(self) -> None:
        captured_log_path: Path | None = None

        def run_command(*_args: object, **kwargs: object) -> SimpleNamespace:
            nonlocal captured_log_path
            output = kwargs["stdout"]
            output.write("command output\nsecond line")
            captured_log_path = Path(output.name)
            return SimpleNamespace(returncode=0)

        with patch.object(
            quasar_cli.subprocess,
            "run",
            side_effect=run_command,
        ) as run:
            result = quasar_cli.execute_cmd("npx tool")

        self.assertEqual(result, "실행 성공:\ncommand output\nsecond line")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], "npx tool")
        self.assertTrue(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertNotIn("creationflags", run.call_args.kwargs)
        self.assertNotIn("timeout", run.call_args.kwargs)
        self.assertIsNotNone(captured_log_path)
        self.assertFalse(captured_log_path.exists())

    def test_command_failure_returns_exit_code_and_output(self) -> None:
        def run_command(*_args: object, **kwargs: object) -> SimpleNamespace:
            kwargs["stdout"].write("command failed")
            return SimpleNamespace(returncode=7)

        with patch.object(
            quasar_cli.subprocess,
            "run",
            side_effect=run_command,
        ):
            result = quasar_cli.execute_cmd("npx tool")

        self.assertEqual(result, "실행 실패 (코드 7):\ncommand failed")


class BrowserMcpCategoryTests(unittest.TestCase):
    @staticmethod
    def _schema(name: str) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": "Browser MCP test tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def test_browser_mcp_tools_are_added_with_web_category(self) -> None:
        browser_schema = self._schema("mcp_browser_navigate_page")
        general_schema = self._schema("mcp_calendar_create_event")
        manager = SimpleNamespace(
            tools={
                "mcp_browser_navigate_page": object(),
                "mcp_calendar_create_event": object(),
            },
            web_control_tool_schemas=Mock(return_value=[browser_schema]),
            tool_schemas=Mock(return_value=[browser_schema, general_schema]),
            browser_control_connection_errors=Mock(return_value={}),
        )

        available = quasar_cli._available_tool_schemas(
            "dynamic",
            {"web"},
            set(),
            manager,
        )
        names = {tool["function"]["name"] for tool in available}

        self.assertIn("mcp_browser_navigate_page", names)
        self.assertNotIn("mcp_calendar_create_event", names)
        manager.web_control_tool_schemas.assert_called_once_with()

        integration_tools = quasar_cli._available_tool_schemas(
            "dynamic",
            {"integrations"},
            set(),
            manager,
        )
        integration_names = {
            tool["function"]["name"] for tool in integration_tools
        }
        self.assertNotIn("mcp_browser_navigate_page", integration_names)

    def test_loading_web_category_describes_browser_mcp_tools(self) -> None:
        manager = SimpleNamespace(
            web_control_tool_schemas=Mock(
                return_value=[self._schema("mcp_browser_click")]
            ),
            browser_control_connection_errors=Mock(return_value={}),
        )

        result = quasar_cli._load_tool_categories(
            ["web"],
            set(),
            manager,
        )

        self.assertIn("mcp_browser_click", result)

    def test_browser_server_is_recognized_from_name_or_tool_set(self) -> None:
        is_browser = quasar_cli.MCPServerManager._is_browser_control_server

        self.assertTrue(is_browser("chrome", {}))
        self.assertTrue(
            is_browser(
                "automation",
                {},
                [{"name": "navigate_page"}, {"name": "take_snapshot"}],
            )
        )
        self.assertFalse(is_browser("calendar", {}, [{"name": "create_event"}]))

    def test_manager_returns_only_tools_from_browser_control_servers(self) -> None:
        manager = object.__new__(quasar_cli.MCPServerManager)
        manager._lock = threading.RLock()
        browser_connection = SimpleNamespace(name="browser")
        calendar_connection = SimpleNamespace(name="calendar")
        manager.tools = {
            "mcp_browser_click": (
                browser_connection,
                "click",
                {"name": "click", "inputSchema": {"type": "object"}},
            ),
            "mcp_calendar_add_event": (
                calendar_connection,
                "add_event",
                {"name": "add_event", "inputSchema": {"type": "object"}},
            ),
        }
        manager.browser_control_servers = {"browser"}
        manager.pending_servers = {}
        manager.connection_errors = {}
        manager.connections = {}
        manager._config = {"mcpServers": {}}

        schemas = manager.web_control_tool_schemas()

        self.assertEqual(
            [schema["function"]["name"] for schema in schemas],
            ["mcp_browser_click"],
        )
        self.assertEqual(manager.find_tool_schemas("click"), [])


if __name__ == "__main__":
    unittest.main()
