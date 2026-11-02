import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from docx import Document
from pptx import Presentation
from pptx.util import Inches

from office_documents import apply_office_replacement, load_office_document
from quasar_cli import CliCollaborationMonitor, handle_cli_command


class OfficeDocumentTests(unittest.TestCase):
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

    def test_watchlist_accepts_only_office_files_inside_workspace(self) -> None:
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
            text_path.write_text("not an Office document", encoding="utf-8")
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
                self.assertEqual(monitor.list_office_documents(), [str(word_path)])
                self.assertIn(".docx와 .pptx", monitor.add_office_document("notes.txt"))
                self.assertIn("작업 폴더 안", monitor.add_office_document(str(Path(temporary_directory).parent / "outside.docx")))
                self.assertIn("제거했습니다", monitor.remove_office_document("notes.docx"))
                self.assertEqual(monitor.list_office_documents(), [])
            finally:
                monitor.close()

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


if __name__ == "__main__":
    unittest.main()
