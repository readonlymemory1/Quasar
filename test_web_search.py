import io
import unittest
from unittest.mock import MagicMock, Mock, patch

import quasar_cli


class WebSearchTests(unittest.TestCase):
    def test_search_parses_results_without_opening_chrome(self) -> None:
        page = b"""
        <div class="result results_links">
          <div class="result__body">
            <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1%26b%3D2">Example <b>Page</b></a></h2>
            <a class="result__snippet">A useful <b>search</b> result.</a>
          </div>
        </div>
        """
        with (
            patch.object(quasar_cli.urllib.request, "urlopen", return_value=io.BytesIO(page)) as urlopen,
            patch.object(quasar_cli, "get_browser") as get_browser,
        ):
            result = quasar_cli.web_search("example query")

        self.assertIn("제목: Example Page", result)
        self.assertIn("링크: https://example.com/page?a=1&b=2", result)
        self.assertIn("내용 요약: A useful search result.", result)
        urlopen.assert_called_once()
        get_browser.assert_not_called()

    def test_search_rejects_empty_and_oversized_queries(self) -> None:
        self.assertIn("검색어를 입력", quasar_cli.web_search("  "))
        self.assertIn("500자 이내", quasar_cli.web_search("a" * 501))

    def test_search_reports_empty_results(self) -> None:
        with patch.object(
            quasar_cli.urllib.request,
            "urlopen",
            return_value=io.BytesIO(b"<html><body>No results</body></html>"),
        ):
            result = quasar_cli.web_search("no matching results")
        self.assertIn("결과를 찾지 못했습니다", result)

    def test_navigation_opens_requested_url_in_controlled_chrome(self) -> None:
        page = Mock()
        page.title = "인천남동고등학교"
        page.current_url = "https://namdong.icehs.kr/main.do"
        with patch.object(quasar_cli, "get_browser", return_value=page) as get_browser:
            result = quasar_cli.run_tool("web_navigate", {"url": "https://namdong.icehs.kr/main.do"})

        get_browser.assert_called_once_with()
        page.get.assert_called_once_with("https://namdong.icehs.kr/main.do")
        self.assertIn("페이지 이동 완료", result)
        self.assertIn("인천남동고등학교", result)

    def test_open_new_tab_creates_tab_before_navigating(self) -> None:
        page = Mock()
        page.title = "Google"
        page.current_url = "https://www.google.com/"
        page.current_window_handle = "ai-tab"
        with (
            patch.object(quasar_cli, "get_browser", return_value=page) as get_browser,
            patch.object(quasar_cli, "browser_ai_window_handles", []),
            patch.object(quasar_cli, "browser_tab_ids", {}),
            patch.object(quasar_cli, "browser_tab_owners", {}),
            patch.object(quasar_cli, "browser_next_tab_id", 1),
            patch.object(quasar_cli, "browser_active_window_handle", None),
        ):
            result = quasar_cli.run_tool("web_open_new_tab", {"url": "https://www.google.com/"})

        get_browser.assert_called_once_with()
        page.switch_to.new_window.assert_called_once_with("tab")
        page.get.assert_called_once_with("https://www.google.com/")
        self.assertIn("새 탭 열기 완료", result)
        self.assertIn("AI 탭 tab-1", result)
        self.assertIn("Google", result)

    def test_list_tabs_identifies_user_and_ai_tabs(self) -> None:
        page = Mock()
        page.window_handles = ["user-tab", "ai-tab"]
        page.title = "Example"
        page.current_url = "https://example.com/"
        with (
            patch.object(quasar_cli, "get_browser", return_value=page),
            patch.object(quasar_cli, "browser_active_window_handle", "ai-tab"),
            patch.object(quasar_cli, "browser_ai_window_handles", ["ai-tab"]),
            patch.object(quasar_cli, "browser_tab_ids", {"user-tab": "tab-1", "ai-tab": "tab-2"}),
            patch.object(quasar_cli, "browser_tab_owners", {"user-tab": "user", "ai-tab": "ai"}),
        ):
            result = quasar_cli.web_list_tabs()

        self.assertIn("tab-1 [사용자]", result)
        self.assertIn("tab-2 [AI]", result)
        self.assertIn("1. tab-1", result)
        self.assertIn("2. tab-2", result)
        page.switch_to.window.assert_any_call("user-tab")
        page.switch_to.window.assert_any_call("ai-tab")
        page.switch_to.window.assert_called_with("ai-tab")

    def test_browser_keeps_using_the_user_selected_tab(self) -> None:
        page = Mock()
        page.window_handles = ["user-tab", "ai-tab"]
        with (
            patch.object(quasar_cli, "browser", page),
            patch.object(quasar_cli, "browser_active_window_handle", "user-tab"),
            patch.object(quasar_cli, "browser_ai_window_handles", ["ai-tab"]),
            patch.object(quasar_cli, "browser_tab_ids", {"user-tab": "tab-1", "ai-tab": "tab-2"}),
            patch.object(quasar_cli, "browser_tab_owners", {"user-tab": "user", "ai-tab": "ai"}),
        ):
            result = quasar_cli.get_browser()

        self.assertIs(result, page)
        page.switch_to.window.assert_called_once_with("user-tab")

    def test_browser_starts_with_only_one_tab(self) -> None:
        page = Mock()
        page.window_handles = ["user-tab"]
        chrome_process = Mock()
        chrome_process.poll.return_value = None
        startup_info = Mock(dwFlags=0)
        with (
            patch.object(quasar_cli, "browser", None),
            patch.object(quasar_cli, "chrome_process", None),
            patch.object(quasar_cli, "browser_user_window_handle", None),
            patch.object(quasar_cli, "browser_active_window_handle", None),
            patch.object(quasar_cli, "browser_ai_window_handles", []),
            patch.object(quasar_cli, "browser_tab_ids", {}),
            patch.object(quasar_cli, "browser_tab_owners", {}),
            patch.object(quasar_cli, "browser_next_tab_id", 1),
            patch.object(quasar_cli.Path, "is_file", return_value=True),
            patch.object(quasar_cli.Path, "mkdir"),
            patch.object(
                quasar_cli.subprocess,
                "STARTUPINFO",
                return_value=startup_info,
            ),
            patch.object(quasar_cli.subprocess, "Popen", return_value=chrome_process),
            patch.object(
                quasar_cli.urllib.request,
                "urlopen",
                return_value=MagicMock(),
            ),
            patch.object(quasar_cli.webdriver, "ChromeOptions", return_value=Mock()),
            patch.object(quasar_cli.webdriver, "Chrome", return_value=page),
            patch.object(quasar_cli, "ChromeDriverManager") as driver_manager,
            patch.object(quasar_cli, "Service"),
        ):
            driver_manager.return_value.install.return_value = "chromedriver"
            result = quasar_cli.get_browser()

            self.assertIs(result, page)
            self.assertEqual(quasar_cli.browser_user_window_handle, "user-tab")
            self.assertEqual(quasar_cli.browser_active_window_handle, "user-tab")
            self.assertEqual(quasar_cli.browser_tab_owners, {"user-tab": "user"})
            self.assertEqual(quasar_cli.browser_ai_window_handles, [])

        page.switch_to.new_window.assert_not_called()

    def test_browser_does_not_reopen_a_tab_when_the_active_tab_was_closed(self) -> None:
        page = Mock()
        page.window_handles = ["remaining-tab"]
        with (
            patch.object(quasar_cli, "browser", page),
            patch.object(quasar_cli, "browser_user_window_handle", "closed-user-tab"),
            patch.object(quasar_cli, "browser_active_window_handle", "closed-ai-tab"),
            patch.object(quasar_cli, "browser_ai_window_handles", ["closed-ai-tab"]),
            patch.object(
                quasar_cli,
                "browser_tab_ids",
                {"closed-user-tab": "tab-1", "closed-ai-tab": "tab-2"},
            ),
            patch.object(
                quasar_cli,
                "browser_tab_owners",
                {"closed-user-tab": "user", "closed-ai-tab": "ai"},
            ),
        ):
            result = quasar_cli.get_browser()

            self.assertIs(result, page)
            self.assertEqual(quasar_cli.browser_active_window_handle, "remaining-tab")
            self.assertIsNone(quasar_cli.browser_user_window_handle)
            self.assertNotIn("closed-ai-tab", quasar_cli.browser_tab_ids)
            self.assertEqual(quasar_cli.browser_ai_window_handles, [])

        page.switch_to.new_window.assert_not_called()
        page.switch_to.window.assert_called_once_with("remaining-tab")

    def test_browser_does_not_create_a_tab_when_all_tabs_are_closed(self) -> None:
        page = Mock()
        page.window_handles = []
        with patch.object(quasar_cli, "browser", page):
            with self.assertRaisesRegex(RuntimeError, "no open tabs"):
                quasar_cli.get_browser()

        page.switch_to.new_window.assert_not_called()

    def test_switch_tab_allows_selected_user_and_ai_tabs(self) -> None:
        page = Mock()
        page.window_handles = ["user-tab", "ai-tab"]
        page.title = "AI page"
        page.current_url = "https://example.com/"
        with (
            patch.object(quasar_cli, "get_browser", return_value=page),
            patch.object(quasar_cli, "browser_active_window_handle", "ai-tab"),
            patch.object(quasar_cli, "browser_ai_window_handles", ["ai-tab"]),
            patch.object(quasar_cli, "browser_tab_ids", {"user-tab": "tab-1", "ai-tab": "tab-2"}),
            patch.object(quasar_cli, "browser_tab_owners", {"user-tab": "user", "ai-tab": "ai"}),
        ):
            result = quasar_cli.web_switch_tab("tab-2")
            user_result = quasar_cli.web_switch_tab("tab-1")
            failure = quasar_cli.web_switch_tab("unknown")

        self.assertIn("탭 전환 완료: tab-2 (AI)", result)
        self.assertIn("사용자 지정", user_result)
        self.assertIn("탭 전환 실패", failure)
        self.assertEqual(
            [call.args[0] for call in page.switch_to.window.call_args_list],
            ["ai-tab", "user-tab"],
        )

    def test_open_new_tab_rejects_invalid_urls_without_opening_browser(self) -> None:
        with patch.object(quasar_cli, "get_browser") as get_browser:
            self.assertIn("전체 URL", quasar_cli.web_open_new_tab("google.com"))
            self.assertIn("공백", quasar_cli.web_open_new_tab("https://example.com/bad path"))
        get_browser.assert_not_called()

    def test_navigation_rejects_invalid_or_credentialed_urls(self) -> None:
        with patch.object(quasar_cli, "get_browser") as get_browser:
            self.assertIn("전체 URL", quasar_cli.web_navigate("namdong.icehs.kr"))
            self.assertIn("공백", quasar_cli.web_navigate("https://example.com/bad path"))
            self.assertIn("계정 정보", quasar_cli.web_navigate("https://user:secret@example.com"))
            self.assertIn("잘못된 URL", quasar_cli.web_navigate("https://["))
        get_browser.assert_not_called()

    def test_navigation_reports_browser_failures(self) -> None:
        page = Mock()
        page.get.side_effect = RuntimeError("navigation failed")
        with patch.object(quasar_cli, "get_browser", return_value=page):
            result = quasar_cli.web_navigate("https://example.com")
        self.assertIn("페이지 이동 실패", result)
        self.assertIn("navigation failed", result)

    def test_web_input_supports_contenteditable_chat_composer(self) -> None:
        element = Mock()
        element.get_attribute.side_effect = lambda name: (
            "true" if name == "contenteditable" else None
        )
        element.get_property.return_value = "안녕하세요"
        page = Mock()
        page.current_url = "https://chatgpt.com/"
        wait = Mock()
        wait.until.return_value = element
        with (
            patch.object(quasar_cli, "get_browser", return_value=page),
            patch.object(quasar_cli, "WebDriverWait", return_value=wait),
        ):
            result = quasar_cli.web_input("#prompt-textarea", "안녕하세요")

        element.click.assert_called_once_with()
        element.clear.assert_not_called()
        element.send_keys.assert_any_call(quasar_cli.Keys.CONTROL, "a")
        element.send_keys.assert_any_call(quasar_cli.Keys.BACKSPACE)
        element.send_keys.assert_any_call("안녕하세요")
        self.assertIn("입력 확인 완료", result)

    def test_web_input_does_not_send_when_value_cannot_be_verified(self) -> None:
        element = Mock()
        element.get_attribute.return_value = None
        element.get_property.return_value = None
        element.text = "다른 내용"
        page = Mock()
        wait = Mock()
        wait.until.return_value = element
        with (
            patch.object(quasar_cli, "get_browser", return_value=page),
            patch.object(quasar_cli, "WebDriverWait", return_value=wait),
        ):
            result = quasar_cli.web_input("#prompt-textarea", "보낼 내용", press_enter=True)

        self.assertIn("입력 확인 실패", result)
        element.send_keys.assert_called_once_with("보낼 내용")


if __name__ == "__main__":
    unittest.main()
