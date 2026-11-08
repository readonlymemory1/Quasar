import tempfile
import unittest
from pathlib import Path

import quasar_cli


class McpConfigPathTests(unittest.TestCase):
    def test_explicit_relative_path_is_resolved_from_workspace(self) -> None:
        workspace = Path("C:/workspace")
        application = Path("C:/quasar")

        self.assertEqual(
            quasar_cli.resolve_mcp_config_path("config/custom.json", workspace, application),
            workspace / "config/custom.json",
        )

    def test_application_config_is_used_when_workspace_config_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            application = root / "application"
            workspace.mkdir()
            application.mkdir()
            application_config = application / ".mcp.json"
            application_config.write_text('{"mcpServers": {}}', encoding="utf-8")

            self.assertEqual(
                quasar_cli.resolve_mcp_config_path(None, workspace, application),
                application_config,
            )

    def test_workspace_config_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            application = root / "application"
            workspace.mkdir()
            application.mkdir()
            workspace_config = workspace / ".mcp.json"
            workspace_config.write_text('{"mcpServers": {}}', encoding="utf-8")
            (application / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

            self.assertEqual(
                quasar_cli.resolve_mcp_config_path(None, workspace, application),
                workspace_config,
            )


if __name__ == "__main__":
    unittest.main()
