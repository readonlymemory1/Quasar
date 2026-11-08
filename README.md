# Quasar

AI 비서 데스크톱 앱과 CLI를 제공하는 Python 프로젝트입니다.

## 구조

```
company_agent/
├── main.py                 # 실행 진입점
├── requirements.txt
├── conftest.py             # pytest용 src/ import 경로 설정
├── run_tests.py            # 전체 테스트 실행
├── src/                    # 애플리케이션 코드
│   ├── Quasar.py           # PySide6 데스크톱 앱
│   ├── quasar_cli.py       # CLI / 에이전트 코어
│   ├── chat_history.py
│   ├── office_documents.py
│   ├── quasar_desktop_control.py
│   ├── quasar_galaxy.py
│   ├── quasar_tool_calling.py
│   └── todo_store.py
├── assets/                 # 아이콘 등 정적 리소스
│   └── quasar_icon.png
├── config/                 # 설정 예시 및 협업 설정
│   ├── .env.example
│   ├── .mcp.json.example
│   └── collaboration_config.json
├── scripts/                # 실행 / 설치 스크립트
│   ├── quasar.bat
│   └── install_libraries.ps1
├── tests/                  # 단위 테스트
└── data/                   # 로컬 데이터 (SQLite)
    └── todolist.sqlite3
```

## 실행

```powershell
# 데스크톱 앱
python main.py

# 또는 런처 사용
scripts\quasar.bat
```

## 설정

- `config/.env.example`을 참고해 프로젝트 루트에 `.env`를 만듭니다.
- `config/.mcp.json.example`을 참고해 프로젝트 루트에 `.mcp.json`을 만듭니다.
- 데스크톱(마우스·키보드) 제어 도구는 기본적으로 꺼져 있습니다.
  설정 창에서 **데스크톱 제어 도구 사용**을 켜야 해당 도구의 설명과
  스키마가 AI에게 전달됩니다.

## 사용 팁

- 작업이 실행 중일 때 입력창에 다음 작업을 입력하고 `Enter`를 누르면
  대기열에 추가됩니다. 현재 작업이 끝나면 순서대로 실행됩니다.
- `Shift+Enter`는 줄바꿈, `Enter`는 전송입니다.
- 작업 중 `중지` 버튼을 누르면 현재 작업과 대기 중인 작업이 함께 취소됩니다.
- 명령 실행 승인은 삭제·덮어쓰기·강제 푸시·시스템 설정 변경처럼 되돌리기
  어려운 명령에만 요청됩니다. `dir`, `git status`, `git log` 같은 조회성
  명령은 승인 없이 바로 실행됩니다.
- 명령을 실행할 때는 채팅과 상태 표시줄에 `도구 실행 중: <실행할 명령어>`로
  어떤 명령어를 쓰는지 표시되고, 승인이 필요하면 승인 창에 명령 원문이
  그대로 나타납니다.

### 병렬 작업 창 (크롬 탭처럼)

- 상단 **새 작업 창**(또는 방사형 메뉴의 **새 작업 창**)을 누르면, 메인 창과
  독립적으로 요청을 실행하는 새 창이 열립니다.
- 한 창에서 작업이 실행되는 동안 다른 창에서 별개의 작업을 동시에 진행할 수
  있습니다. 각 창은 대화·대기열·실행 상태를 따로 가지며, 공급자·MCP·데스크톱
  세션 같은 공용 자원은 메인 창과 공유합니다.
- 도구 진행 메시지와 위험 작업 승인은 요청을 실행한 창으로 전달됩니다.
- 새 작업 창은 채팅 기록에 저장하지 않는 임시 작업 공간입니다.
- 메인 창을 닫으면 함께 닫히고, 실행 중이던 요청은 취소됩니다.

## 테스트

```powershell
python run_tests.py
# 또는
python -m unittest discover -s tests -t .
# 또는
pytest
```
