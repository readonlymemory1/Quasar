import sys
from pathlib import Path

# src/ 아래의 애플리케이션 모듈을 import 경로에 추가합니다.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from Quasar import main  # noqa: E402


if __name__ == "__main__":
    main()
