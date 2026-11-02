from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from pptx import Presentation


SUPPORTED_OFFICE_SUFFIXES = {".docx", ".pptx"}


@dataclass
class OfficeTextSegment:
    reference: str
    text: str
    paragraph: Any


@dataclass
class OfficeDocument:
    source_hash: str
    segments: list[OfficeTextSegment]
    document: Any


def _append_word_table_segments(
    segments: list[OfficeTextSegment],
    table: Any,
    reference: str,
) -> None:
    for row_index, row in enumerate(table.rows):
        for cell_index, cell in enumerate(row.cells):
            cell_reference = f"{reference}/r/{row_index}/c/{cell_index}"
            for paragraph_index, paragraph in enumerate(cell.paragraphs):
                text = paragraph.text
                if text.strip():
                    segments.append(
                        OfficeTextSegment(
                            f"{cell_reference}/p/{paragraph_index}",
                            text,
                            paragraph,
                        )
                    )
            for nested_index, nested_table in enumerate(cell.tables):
                _append_word_table_segments(
                    segments,
                    nested_table,
                    f"{cell_reference}/t/{nested_index}",
                )


def _read_word_document(data: bytes, source_hash: str) -> OfficeDocument:
    document = Document(BytesIO(data))
    segments: list[OfficeTextSegment] = []
    for paragraph_index, paragraph in enumerate(document.paragraphs):
        if paragraph.text.strip():
            segments.append(
                OfficeTextSegment(f"word/p/{paragraph_index}", paragraph.text, paragraph)
            )
    for table_index, table in enumerate(document.tables):
        _append_word_table_segments(segments, table, f"word/t/{table_index}")
    return OfficeDocument(source_hash, segments, document)


def _append_presentation_table_segments(
    segments: list[OfficeTextSegment],
    table: Any,
    reference: str,
) -> None:
    for row_index, row in enumerate(table.rows):
        for cell_index, cell in enumerate(row.cells):
            cell_reference = f"{reference}/r/{row_index}/c/{cell_index}"
            for paragraph_index, paragraph in enumerate(cell.text_frame.paragraphs):
                text = paragraph.text
                if text.strip():
                    segments.append(
                        OfficeTextSegment(
                            f"{cell_reference}/p/{paragraph_index}",
                            text,
                            paragraph,
                        )
                    )


def _read_presentation(data: bytes, source_hash: str) -> OfficeDocument:
    presentation = Presentation(BytesIO(data))
    segments: list[OfficeTextSegment] = []
    for slide_index, slide in enumerate(presentation.slides):
        for shape_index, shape in enumerate(slide.shapes):
            shape_reference = f"pptx/s/{slide_index + 1}/sh/{shape_index}"
            if shape.has_text_frame:
                for paragraph_index, paragraph in enumerate(shape.text_frame.paragraphs):
                    text = paragraph.text
                    if text.strip():
                        segments.append(
                            OfficeTextSegment(
                                f"{shape_reference}/p/{paragraph_index}",
                                text,
                                paragraph,
                            )
                        )
            if shape.has_table:
                _append_presentation_table_segments(
                    segments,
                    shape.table,
                    f"{shape_reference}/t",
                )
    return OfficeDocument(source_hash, segments, presentation)


def load_office_document(path: Path, data: bytes | None = None) -> OfficeDocument:
    source = path.read_bytes() if data is None else data
    source_hash = hashlib.sha256(source).hexdigest()
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_word_document(source, source_hash)
    if suffix == ".pptx":
        return _read_presentation(source, source_hash)
    raise ValueError(f"지원하지 않는 Office 문서 형식입니다: {path.suffix}")


def _replace_segment_text(segment: OfficeTextSegment, replacement: str) -> None:
    paragraph = segment.paragraph
    if hasattr(paragraph, "clear"):
        existing_runs = list(paragraph.runs)
        run_properties = (
            deepcopy(existing_runs[0]._r.rPr)
            if existing_runs and existing_runs[0]._r.rPr is not None
            else None
        )
        paragraph.clear()
        run = paragraph.add_run()
        run.text = replacement
        if run_properties is not None:
            run._r.insert(0, run_properties)
        return
    paragraph.text = replacement


def _office_lock_paths(path: Path) -> tuple[Path, ...]:
    names = {f"~${path.name}"}
    if len(path.name) > 2:
        names.add(f"~${path.name[2:]}")
    return tuple(path.with_name(name) for name in names)


def apply_office_replacement(
    path: Path,
    expected_hash: str,
    target_reference: str,
    expected_text: str,
    replacement: str,
) -> Path:
    if any(lock_path.exists() for lock_path in _office_lock_paths(path)):
        raise PermissionError(
            "문서가 Word 또는 PowerPoint에서 열려 있는 것 같습니다. "
            "앱에서 문서를 닫은 뒤 승인 명령을 다시 실행해 주세요."
        )
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != expected_hash:
        raise ValueError("문서가 제안 이후 변경되어 수정하지 않았습니다. 다시 검토해 주세요.")

    office_document = load_office_document(path, original)
    matches = [
        segment
        for segment in office_document.segments
        if segment.reference == target_reference and segment.text == expected_text
    ]
    if len(matches) != 1:
        raise ValueError("수정 대상 문단이 현재 문서와 일치하지 않습니다. 다시 검토해 주세요.")

    _replace_segment_text(matches[0], replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=path.suffix,
            prefix=f".{path.stem}.quasar-",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        office_document.document.save(str(temporary_path))
        verified = load_office_document(temporary_path)
        if not any(
            segment.reference == target_reference and segment.text == replacement
            for segment in verified.segments
        ):
            raise OSError("저장한 문서에서 수정 내용을 확인하지 못했습니다.")

        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("저장 직전에 문서가 변경되어 수정하지 않았습니다.")
        if any(lock_path.exists() for lock_path in _office_lock_paths(path)):
            raise PermissionError(
                "문서가 Word 또는 PowerPoint에서 열려 있어 수정하지 않았습니다."
            )

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = path.with_name(
            f"{path.stem}.quasar-backup-{timestamp}{path.suffix}"
        )
        shutil.copy2(path, backup_path)
        if hashlib.sha256(backup_path.read_bytes()).hexdigest() != expected_hash:
            raise OSError(f"원본 백업 확인에 실패했습니다: {backup_path}")

        os.replace(temporary_path, path)
        temporary_path = None
        return backup_path
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
