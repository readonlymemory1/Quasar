import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import quasar_cli
import quasar_tool_calling


class CodeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.directory_patch = patch.object(quasar_cli, "current_directory", self.workspace)
        self.directory_patch.start()

    def tearDown(self) -> None:
        self.directory_patch.stop()
        self.temp_dir.cleanup()

    def test_code_analysis_category_contains_new_tool_schemas(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in quasar_tool_calling.tools_for_categories(
                quasar_cli.TOOLS,
                {"code_analysis"},
            )
        }
        self.assertTrue(
            {"grep", "glob", "python_ast_outline", "git_diff", "lsp_query"} <= names
        )

    def test_integrations_category_exposes_mcp_management_tools(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in quasar_tool_calling.tools_for_categories(
                quasar_cli.TOOLS,
                {"integrations"},
            )
        }
        self.assertTrue(
            {
                "list_mcp_servers",
                "add_mcp_server",
                "remove_mcp_server",
                "discover_mcp_tools",
            } <= names
        )

    def test_glob_and_grep_match_workspace_files_without_generated_directories(self) -> None:
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "sample.py").write_text(
            "def target():\n    return 'needle'\n",
            encoding="utf-8",
        )
        excluded = self.workspace / ".venv"
        excluded.mkdir()
        (excluded / "hidden.py").write_text("needle\n", encoding="utf-8")

        glob_result = quasar_cli.workspace_glob("**/*.py")
        grep_result = quasar_cli.grep_workspace("needle", "**/*.py")

        self.assertIn("src\\sample.py", glob_result)
        self.assertNotIn(".venv", glob_result)
        self.assertIn("src\\sample.py:2", grep_result)
        self.assertNotIn(".venv", grep_result)

    def test_workspace_search_rejects_parent_path_patterns(self) -> None:
        self.assertIn("상대 패턴", quasar_cli.workspace_glob("../*.py"))
        self.assertIn("상대 파일 패턴", quasar_cli.grep_workspace("x", "../*.py"))

    def test_python_ast_outline_lists_nested_classes_and_functions(self) -> None:
        source_path = self.workspace / "sample.py"
        source_path.write_text(
            "class Example:\n"
            "    async def run(self, value):\n"
            "        def nested():\n"
            "            return value\n",
            encoding="utf-8",
        )

        result = quasar_cli.python_ast_outline("sample.py")

        self.assertIn('"name": "Example"', result)
        self.assertIn('"name": "Example.run"', result)
        self.assertIn('"name": "Example.run.nested"', result)
        self.assertIn('"async": true', result)

    def test_git_diff_uses_head_and_can_limit_to_workspace_file(self) -> None:
        expected = SimpleNamespace(returncode=0, stdout="diff --git a/sample.py", stderr="")
        with patch.object(quasar_cli.subprocess, "run", return_value=expected) as run:
            result = quasar_cli.git_diff("sample.py")

        self.assertEqual(result, expected.stdout)
        command = run.call_args.args[0]
        self.assertIn("HEAD", command)
        self.assertEqual(command[-1], "sample.py")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_lsp_request_waits_for_and_returns_vscode_response(self) -> None:
        source_path = self.workspace / "sample.py"
        source_path.write_text("value = 1\n", encoding="utf-8")
        server = quasar_cli.CliLiveAnalysisServer(self.workspace)
        response: list[str] = []
        worker = threading.Thread(
            target=lambda: response.append(
                server.query_lsp("sample.py", "hover", 1, 1)
            ),
            daemon=True,
        )
        try:
            worker.start()
            deadline = time.monotonic() + 2
            request = None
            while time.monotonic() < deadline and request is None:
                request = server.pending_lsp_request()
                time.sleep(0.01)
            self.assertIsNotNone(request)
            server.respond_lsp_request(
                request["id"],
                [{"contents": ["int"]}],
                None,
            )
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(json.loads(response[0]), [{"contents": ["int"]}])
        finally:
            server.close()

    def test_lsp_query_rejects_invalid_position_and_external_path(self) -> None:
        (self.workspace / "sample.py").write_text("value = 1\n", encoding="utf-8")
        server = quasar_cli.CliLiveAnalysisServer(self.workspace)
        try:
            self.assertIn(
                "line과 character",
                server.query_lsp("sample.py", "definition", 0, 0),
            )
            self.assertIn(
                "바깥",
                server.query_lsp("../outside.py", "diagnostics"),
            )
        finally:
            server.close()

    def test_mcp_server_can_be_added_and_removed_without_restarting_manager(self) -> None:
        config_path = self.workspace / ".mcp.json"
        config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
        manager = quasar_cli.MCPServerManager(config_path)
        previous_callback = quasar_cli.tool_approval_callback
        approved_actions = []

        class FakeConnection:
            def __init__(self, name, _config):
                del _config
                self.name = name
                self.closed = False

            def list_tools(self):
                return [{"name": "lookup", "inputSchema": {"type": "object"}}]

            def close(self):
                self.closed = True

        try:
            def approve_mcp_change(name, arguments):
                del arguments
                approved_actions.append(name)
                return True

            quasar_cli.tool_approval_callback = (
                approve_mcp_change
            )
            with patch.object(quasar_cli, "MCPStdioConnection", FakeConnection):
                added = quasar_cli.run_tool(
                    "add_mcp_server",
                    {
                        "server_name": "sample",
                        "server_config": {
                            "command": "python",
                            "args": ["server.py"],
                            "env": {},
                        },
                    },
                    mcp_manager=manager,
                )
                self.assertIn("추가하고 연결", added)
                self.assertEqual(len(manager.tool_schemas()), 1)
                self.assertEqual(manager.list_servers()[0]["status"], "connected")
                self.assertEqual(
                    json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"][
                        "sample"
                    ]["command"],
                    "python",
                )

                connection = manager.connections["sample"]
                removed = quasar_cli.run_tool(
                    "remove_mcp_server",
                    {"server_name": "sample"},
                    mcp_manager=manager,
                )

            self.assertIn("제거하고 연결을 종료", removed)
            self.assertTrue(connection.closed)
            self.assertEqual(manager.tool_schemas(), [])
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"], {})
            self.assertEqual(
                approved_actions,
                ["add_mcp_server", "remove_mcp_server"],
            )
        finally:
            quasar_cli.tool_approval_callback = previous_callback
            manager.close()

    def test_mcp_startup_failure_is_retained_as_exact_diagnostic(self) -> None:
        config_path = self.workspace / ".mcp.json"
        config_path.write_text(
            json.dumps({
                "mcpServers": {
                    "fetch": {
                        "command": "npx",
                        "args": ["-y", "@example/mcp"],
                    }
                }
            }),
            encoding="utf-8",
        )

        with (
            patch.object(
                quasar_cli,
                "MCPStdioConnection",
                side_effect=FileNotFoundError(
                    2,
                    "The system cannot find the file specified",
                    "npx",
                ),
            ),
            patch("sys.stderr"),
        ):
            manager = quasar_cli.MCPServerManager(config_path)

        try:
            servers = manager.list_servers()
            self.assertEqual(servers[0]["status"], "configured_but_disconnected")
            self.assertIn("npx", servers[0]["error"])
            self.assertIn("2", servers[0]["error"])
            self.assertEqual(
                manager.connection_errors["fetch"],
                servers[0]["error"],
            )
        finally:
            manager.close()

    def test_mcp_server_add_requires_approval_and_redacts_env_values(self) -> None:
        config_path = self.workspace / ".mcp.json"
        config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
        manager = quasar_cli.MCPServerManager(config_path)
        approval_requests = []
        previous_callback = quasar_cli.tool_approval_callback

        class FakeConnection:
            def __init__(self, *_args):
                del _args
                self.started = True

            def list_tools(self):
                return []

            def close(self):
                pass

        try:
            quasar_cli.tool_approval_callback = (
                lambda name, arguments: approval_requests.append((name, arguments))
                or False
            )
            with patch.object(quasar_cli, "MCPStdioConnection", FakeConnection):
                result = quasar_cli.run_tool(
                    "add_mcp_server",
                    {
                        "server_name": "sample",
                        "server_config": {
                            "command": "python",
                            "args": ["server.py"],
                            "env": {"API_TOKEN": "private-value"},
                        },
                    },
                    mcp_manager=manager,
                )

            self.assertIn("승인되지 않았습니다", result)
            self.assertEqual(manager.connections, {})
            self.assertEqual(approval_requests[0][0], "add_mcp_server")
            self.assertEqual(
                approval_requests[0][1]["server_config"]["env"]["API_TOKEN"],
                "[hidden]",
            )
            self.assertNotIn(
                "private-value",
                config_path.read_text(encoding="utf-8"),
            )
        finally:
            quasar_cli.tool_approval_callback = previous_callback
            manager.close()


if __name__ == "__main__":
    unittest.main()
