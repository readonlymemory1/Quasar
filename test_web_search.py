import io
import unittest
from unittest.mock import Mock, patch

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
