# Quasar 할 일 데스크톱 앱

Windows용 Tkinter 할 일 관리 앱입니다. Quasar CLI 및 브라우저 할 일 앱과 같은 SQLite 목록을 사용합니다.

## 실행

- `QuasarTodo.exe`가 있으면 더블클릭해 실행합니다.
- 소스에서 실행하려면 Python 3.10 이상을 설치한 뒤 `run.bat`을 실행합니다.
- 실행 파일을 다시 만들려면 PyInstaller가 설치된 Python에서 `build_exe.bat`을 실행합니다.

체크박스를 클릭해 완료하거나 다시 진행 중으로 바꾸세요. 입력란에 할 일을 쓰고 Enter를 누르면 추가됩니다. 항목을 두 번 클릭하면 상세 정보를 수정할 수 있고, 오른쪽 `×`를 누르면 삭제할 수 있습니다. Quasar나 브라우저에서 바뀐 목록도 5초마다 반영합니다.

데이터는 `%LOCALAPPDATA%\QuasarTodo\todolist.sqlite3`에 저장됩니다.
