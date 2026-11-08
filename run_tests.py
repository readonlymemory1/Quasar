"""프로젝트 루트에서 전체 unittest를 실행합니다.

사용법:
    python run_tests.py

GUI 테스트 중 일부가 표준 stderr 스트림을 닫아 버려 실행 요약이 사라지는
문제가 있어, 결과를 버퍼에 모았다가 마지막에 stdout으로 직접 출력합니다.
"""

import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _write_utf8(text: str) -> None:
    """Windows 콘솔(cp949)에서도 한글이 깨지지 않게 출력한다."""
    data = text.encode("utf-8", "replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(data)
            buffer.flush()
            return
        except (OSError, ValueError):
            pass
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _build_summary(result: unittest.TestResult) -> str:
    lines = [
        "",
        "=" * 60,
        f"실행한 테스트: {result.testsRun}개",
        f"실패: {len(result.failures)}개 · 오류: {len(result.errors)}개",
    ]
    for label, entries in (("실패", result.failures), ("오류", result.errors)):
        for name, _traceback in entries:
            lines.append(f"  [{label}] {name}")
    if result.wasSuccessful():
        lines.append("결과: 전체 통과 ✅")
    else:
        lines.append("결과: 실패 또는 오류가 있습니다 ❌")
    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


def main() -> int:
    suite = unittest.TestLoader().discover(
        start_dir=str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    report = io.StringIO()
    result = unittest.TextTestRunner(stream=report, verbosity=2).run(suite)
    _write_utf8(report.getvalue())
    _write_utf8(_build_summary(result))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    exit_code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    # GUI 테스트(qt) 객체가 인터프리터 종료 시 정리되며 간헐적으로 접근 위반으로
    # 프로세스가 죽는다. 결과 요약은 이미 출력했으므로 정리 단계를 건너뛰고
    # 테스트 결과 코드로 바로 종료한다.
    os._exit(exit_code)
