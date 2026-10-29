import io
import unittest
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


if __name__ == "__main__":
    unittest.main()
