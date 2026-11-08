import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from docx import Document
from pptx import Presentation
from pptx.util import Inches

from office_documents import apply_office_replacement, load_office_document
from quasar_cli import (
    CliCollaborationMonitor,
    handle_cli_command,
    parse_json_object_response,
    request_agent,
)


class OfficeDocumentTests(unittest.TestCase):
    def test_parses_json_object_with_markdown_or_surrounding_text(self) -> None:
        expected = {"questions": [], "edits": []}
        for answer in (
            '{"questions":[],"edits":[]}',
            '```json\n{"questions":[],"edits":[]}\n```',
            '검토 결과입니다:\n{"questions":[],"edits":[]} 감사합니다.',
            '\ufeff{"questions":[],"edits":[]}',
        ):
            with self.subTest(answer=answer):
                self.assertEqual(parse_json_object_response(answer), expected)

    def test_rejects_response_without_a_json_object(self) -> None:
        for answer in ("문서에 문제가 없습니다.", '[{"questions":[]}]'):
            with self.subTest(answer=answer):
                with self.assertRaises(json.JSONDecodeError):
                    parse_json_object_response(answer)

    def test_reads_and_approves_notepad_text_replacement_preserving_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.txt"
            original = "첫 번째 줄\r\n두 번째 줄\r\n".encode("utf-8-sig")
            path.write_bytes(original)

            snapshot = load_office_document(path)
            second_line = snapshot.segments[1]
            backup = apply_office_replacement(
                path,
                snapshot.source_hash,
                second_line.reference,
                second_line.text,
                "승인된 수정 내용",
            )

            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(
                path.read_bytes(),
                "첫 번째 줄\r\n승인된 수정 내용\r\n".encode("utf-8-sig"),
            )

    def test_rejects_notepad_replacement_when_file_changed_after_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.txt"
            path.write_text("Original\n", encoding="utf-8")
            snapshot = load_office_document(path)
            path.write_text("Changed by user\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "문서가 제안 이후 변경"):
                apply_office_replacement(
                    path,
                    snapshot.source_hash,
                    snapshot.segments[0].reference,
                    snapshot.segments[0].text,
                    "AI replacement",
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "Changed by user\n")

    def test_reads_and_approves_word_paragraph_replacement_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.docx"
            document = Document()
            document.add_paragraph("Old wording")
            document.save(path)

            snapshot = load_office_document(path)
            paragraph = snapshot.segments[0]
            backup = apply_office_replacement(
                path,
                snapshot.source_hash,
                paragraph.reference,
                paragraph.text,
                "Approved wording",
            )

            self.assertTrue(backup.is_file())
            self.assertEqual(Document(backup).paragraphs[0].text, "Old wording")
            self.assertEqual(Document(path).paragraphs[0].text, "Approved wording")

    def test_rejects_word_document_changed_after_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.docx"
            document = Document()
            document.add_paragraph("Original")
            document.save(path)
            snapshot = load_office_document(path)

            changed_document = Document(path)
            changed_document.paragraphs[0].text = "Changed by user"
            changed_document.save(path)

            with self.assertRaisesRegex(ValueError, "문서가 제안 이후 변경"):
                apply_office_replacement(
                    path,
                    snapshot.source_hash,
                    snapshot.segments[0].reference,
                    snapshot.segments[0].text,
                    "AI replacement",
                )
            self.assertEqual(Document(path).paragraphs[0].text, "Changed by user")

    def test_refuses_to_edit_a_document_open_in_office(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.docx"
            document = Document()
            document.add_paragraph("Original")
            document.save(path)
            snapshot = load_office_document(path)
            lock_file = path.with_name(f"~${path.name}")
            lock_file.touch()

            with self.assertRaisesRegex(PermissionError, "문서를 닫은 뒤"):
                apply_office_replacement(
                    path,
                    snapshot.source_hash,
                    snapshot.segments[0].reference,
                    snapshot.segments[0].text,
                    "AI replacement",
                )
            self.assertEqual(Document(path).paragraphs[0].text, "Original")

    def test_reads_and_approves_powerpoint_text_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "slides.pptx"
            presentation = Presentation()
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            shape = slide.shapes.add_textbox(
                Inches(1),
                Inches(1),
                Inches(4),
                Inches(1),
            )
            shape.text_frame.text = "Old slide title"
            presentation.save(path)

            snapshot = load_office_document(path)
            paragraph = snapshot.segments[0]
            backup = apply_office_replacement(
                path,
                snapshot.source_hash,
                paragraph.reference,
                paragraph.text,
                "Approved slide title",
            )

            self.assertTrue(backup.is_file())
            updated = Presentation(path)
            self.assertEqual(
                updated.slides[0].shapes[0].text_frame.text,
                "Approved slide title",
            )

    def test_watchlist_accepts_supported_documents_inside_workspace(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            word_path = workspace / "notes.docx"
            Document().save(word_path)
            text_path = workspace / "notes.txt"
            text_path.write_text("Notepad text", encoding="utf-8")
            markdown_path = workspace / "notes.md"
            markdown_path.write_text("not a supported format", encoding="utf-8")
            monitor = CliCollaborationMonitor(
                client=None,
                model="unused",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                self.assertIn(str(word_path), monitor.add_office_document("notes.docx"))
                self.assertIn(str(text_path), monitor.add_office_document("notes.txt"))
                self.assertEqual(
                    monitor.list_office_documents(),
                    [str(word_path), str(text_path)],
                )
                self.assertIn(".docx, .pptx, .txt", monitor.add_office_document("notes.md"))
                self.assertIn("작업 폴더 안", monitor.add_office_document(str(Path(temporary_directory).parent / "outside.docx")))
                self.assertIn("제거했습니다", monitor.remove_office_document("notes.docx"))
                self.assertEqual(monitor.list_office_documents(), [str(text_path)])
            finally:
                monitor.close()

    def test_registered_documents_are_returned_as_bounded_chat_context(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.docx"
            document = Document()
            document.add_paragraph("Question-specific detail " * 40)
            document.save(path)
            text_path = workspace / "notes.txt"
            text_path.write_text("Notepad-specific detail\n", encoding="utf-8")
            monitor = CliCollaborationMonitor(
                client=None,
                model="unused",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.add_office_document("notes.docx")
                monitor.add_office_document("notes.txt")
                context, errors, included_documents = monitor.office_chat_context()

                self.assertIn("Question-specific detail", context)
                self.assertIn("Notepad-specific detail", context)
                self.assertIn("[연결된 문서 문맥]", context)
                self.assertEqual(errors, [])
                self.assertEqual(included_documents, 2)

                limited_context, _, _ = monitor.office_chat_context(max_chars=180)
                self.assertLess(len(limited_context), 500)
                self.assertIn("문맥 길이 제한", limited_context)
            finally:
                monitor.close()

    def test_request_agent_passes_office_context_without_saving_it_to_history(self) -> None:
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="문서에서 확인했습니다.",
                        tool_calls=None,
                    )
                )
            ]
        )
        messages = [{"role": "system", "content": "system prompt"}]

        with redirect_stdout(StringIO()):
            answer = request_agent(
                client,
                "gpt-4o",
                messages,
                "이 문서에 뭐라고 적혀 있어?",
                office_context="\n[연결 문서]\n핵심 내용",
            )

        request_messages = client.chat.completions.create.call_args.kwargs[
            "messages"
        ]
        office_system_contexts = [
            message["content"]
            for message in request_messages
            if message["role"] == "system" and "연결 문서" in message["content"]
        ]
        self.assertEqual(len(office_system_contexts), 1)
        self.assertIn("핵심 내용", office_system_contexts[0])
        self.assertEqual(messages[-2], {
            "role": "user",
            "content": "이 문서에 뭐라고 적혀 있어?",
        })
        self.assertEqual(answer, "문서에서 확인했습니다.")

    def test_review_queues_questions_and_requires_explicit_approval(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.docx"
            document = Document()
            document.add_paragraph("Old wording")
            document.save(path)
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "questions": ["이 문구의 의미를 확인해 주세요."],
                                    "edits": [
                                        {
                                            "target": snapshot.segments[0].reference,
                                            "replacement": "Suggested wording",
                                            "reason": "표현을 명확하게 합니다.",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        )
                    )
                ]
            )
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.set_enabled(True)
                self.assertTrue(monitor._review_office_document(path, snapshot))
                self.assertEqual(
                    monitor.take_pending_questions(),
                    ["[notes.docx] 이 문구의 의미를 확인해 주세요."],
                )
                self.assertEqual(Document(path).paragraphs[0].text, "Old wording")
                with redirect_stdout(StringIO()):
                    handle_cli_command(
                        "/collab office approve 1",
                        [],
                        collaboration_monitor=monitor,
                    )
                self.assertEqual(Document(path).paragraphs[0].text, "Suggested wording")
            finally:
                monitor.close()

    def test_notepad_review_proposes_and_applies_a_single_line_edit(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_bytes("Original line\r\n".encode("utf-8-sig"))
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "questions": ["이 문장의 뜻을 확인해 주세요."],
                                    "edits": [
                                        {
                                            "target": snapshot.segments[0].reference,
                                            "replacement": "Improved line",
                                            "reason": "더 명확한 표현입니다.",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        )
                    )
                ]
            )
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.set_enabled(True)
                self.assertTrue(monitor._review_office_document(path, snapshot))
                self.assertEqual(
                    monitor.take_pending_questions(),
                    ["[notes.txt] 이 문장의 뜻을 확인해 주세요."],
                )
                command_output: list[str] = []
                handle_cli_command(
                    "/collab file approve 1",
                    [],
                    collaboration_monitor=monitor,
                    output=command_output.append,
                )
                result = "\n".join(command_output)
                self.assertIn("적용했습니다", result)
                self.assertEqual(
                    path.read_bytes(),
                    "Improved line\r\n".encode("utf-8-sig"),
                )
                with redirect_stdout(StringIO()):
                    monitor._check_office_documents()
                client.chat.completions.create.assert_called_once()
            finally:
                monitor.close()

    def test_notepad_review_retries_an_empty_model_response(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("A note to review.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.side_effect = [
                SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"questions":[],"edits":[]}'
                            )
                        )
                    ]
                ),
            ]
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.set_enabled(True)
                self.assertTrue(monitor._review_office_document(path, snapshot))

                self.assertEqual(client.chat.completions.create.call_count, 2)
                for call in client.chat.completions.create.call_args_list:
                    self.assertEqual(
                        call.kwargs["response_format"],
                        {"type": "json_object"},
                    )
                self.assertEqual(monitor.take_pending_questions(), [])
            finally:
                monitor.close()

    def test_notepad_review_reports_repeated_empty_model_responses(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("A note to review.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
            )
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            errors = StringIO()
            try:
                monitor.set_enabled(True)
                with redirect_stderr(errors):
                    self.assertFalse(
                        monitor._review_office_document(path, snapshot)
                    )

                self.assertEqual(client.chat.completions.create.call_count, 2)
                self.assertIn("2회 요청에도 빈 응답", errors.getvalue())
                self.assertNotIn("Expecting value", errors.getvalue())
            finally:
                monitor.close()

    def test_notepad_review_requests_json_from_responses_api(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("A note to review.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.responses.create.return_value = SimpleNamespace(
                output_text='{"questions":[],"edits":[]}'
            )
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-6-test",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.set_enabled(True)
                self.assertTrue(monitor._review_office_document(path, snapshot))

                self.assertEqual(client.responses.create.call_count, 1)
                self.assertEqual(
                    client.responses.create.call_args.kwargs["text"],
                    {"format": {"type": "json_object"}},
                )
            finally:
                monitor.close()

    def test_notepad_review_retries_a_non_json_model_response(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("A note to review.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.side_effect = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="not JSON")
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"questions":[],"edits":[]}'
                            )
                        )
                    ]
                ),
            ]
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            errors = StringIO()
            try:
                monitor.set_enabled(True)
                with redirect_stderr(errors):
                    self.assertTrue(
                        monitor._review_office_document(path, snapshot)
                    )

                self.assertEqual(client.chat.completions.create.call_count, 2)
                self.assertEqual(errors.getvalue(), "")
                self.assertNotIn("Expecting value", errors.getvalue())
            finally:
                monitor.close()

    def test_failed_json_retry_reports_model_not_document_error(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("A note to review.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.side_effect = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="No changes needed.")
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="Still not JSON.")
                        )
                    ]
                ),
            ]
            monitor = CliCollaborationMonitor(
                client=client,
                model="gemma4:e2b",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            errors = StringIO()
            try:
                monitor.set_enabled(True)
                with redirect_stderr(errors):
                    self.assertFalse(
                        monitor._review_office_document(path, snapshot)
                    )

                self.assertEqual(client.chat.completions.create.call_count, 2)
                self.assertIn("gemma4:e2b", errors.getvalue())
                self.assertIn("2회 요청에도 JSON 객체를 반환하지 않았습니다", errors.getvalue())
                self.assertNotIn("Expecting value", errors.getvalue())
            finally:
                monitor.close()

    def test_bad_followup_review_does_not_discard_a_valid_proposal(self) -> None:
        class LiveAnalysisServer:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def latest_document(self):
                return None

        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / "notes.txt"
            path.write_text("Original line.\n", encoding="utf-8")
            snapshot = load_office_document(path)
            client = Mock()
            client.chat.completions.create.side_effect = [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps(
                                    {
                                        "questions": ["문장 의미를 확인해 주세요."],
                                        "edits": [
                                            {
                                                "target": snapshot.segments[0].reference,
                                                "replacement": "Improved line.",
                                                "reason": "명확히 표현합니다.",
                                            }
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="invalid follow-up")
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="still invalid")
                        )
                    ]
                ),
            ]
            monitor = CliCollaborationMonitor(
                client=client,
                model="gpt-4o",
                live_analysis_server=LiveAnalysisServer(workspace),
                settings={
                    "poll_interval_seconds": 1000,
                    "stable_seconds": 1,
                    "idle_seconds": 2,
                },
            )
            try:
                monitor.set_enabled(True)
                with redirect_stdout(StringIO()):
                    self.assertTrue(monitor._review_office_document(path, snapshot))
                    self.assertIn("적용했습니다", monitor.approve_office_proposal(1))

                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    monitor._check_office_documents()

                self.assertEqual(client.chat.completions.create.call_count, 1)
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    "Improved line.\n",
                )
            finally:
                monitor.close()


if __name__ == "__main__":
    unittest.main()
