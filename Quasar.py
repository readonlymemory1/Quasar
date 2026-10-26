import os
import json
import ast
import subprocess
import platform
import sys
import shutil
import secrets
import tokenize
import time
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI
from dotenv import load_dotenv

# 음성 인식 라이브러리
try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    HAS_SR = False

# Selenium 관련 라이브러리
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

if platform.system() == "Windows":
    import winsound

# ==========================================
# 1. 페이지 설정 및 환경변수 로드
# ==========================================
st.set_page_config(
    page_title="IT 운영 & 웹 보조 AI 에이전트",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

load_dotenv(Path(__file__).with_name(".env"))

f2_voice = components.declare_component(
    "f2_voice",
    path=str(Path(__file__).with_name("f2_voice_component")),
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

:root {
    --ink: #edf4f4;
    --muted: #8ea3a5;
    --line: rgba(151, 190, 188, 0.16);
    --panel: #111b1d;
    --panel-strong: #172527;
    --accent: #80e0c2;
    --accent-strong: #4fc9a6;
}

.stApp {
    background: #0b1214;
    background-image: linear-gradient(135deg, rgba(65, 122, 113, 0.13), transparent 35%),
                      linear-gradient(315deg, rgba(30, 75, 82, 0.16), transparent 42%);
    color: var(--ink);
    font-family: 'DM Sans', sans-serif;
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] {
    background: #0d1719;
    border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] > div:first-child { padding: 2rem 1.25rem; }

h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; letter-spacing: 0; }
h1 { font-size: 2.45rem !important; line-height: 1.05 !important; margin-bottom: 0.35rem !important; }
[data-testid="stCaptionContainer"] p { color: var(--muted); }

[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(17, 27, 29, 0.72);
    border: 1px solid var(--line);
    border-radius: 8px;
}

[data-testid="stChatMessage"] {
    background: transparent;
    border: 0;
    padding: 0.65rem 0;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.85rem 1rem;
}

button[kind="secondary"], button[kind="primary"] {
    border-radius: 6px !important;
    min-height: 2.55rem;
    font-weight: 600;
    transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
}
button[kind="secondary"] {
    background: rgba(23, 37, 39, 0.76) !important;
    border: 1px solid var(--line) !important;
    color: var(--ink) !important;
}
button[kind="secondary"]:hover {
    background: var(--panel-strong) !important;
    border-color: var(--accent) !important;
    transform: translateY(-1px);
}
button[kind="primary"] {
    background: var(--accent-strong) !important;
    color: #081312 !important;
    border: 0 !important;
}

[data-testid="stChatInput"] {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel);
}
[data-testid="stChatInput"]:focus-within { border-color: var(--accent); }
input, textarea { font-family: 'DM Sans', sans-serif !important; }
hr { border-color: var(--line); }
</style>
""", unsafe_allow_html=True)

# AI 공급자 초기화
default_ai_provider = os.getenv("AI_PROVIDER", "openai").lower()
if "use_ollama" not in st.session_state:
    st.session_state.use_ollama = default_ai_provider == "ollama"

st.sidebar.toggle(
    "Ollama 사용",
    key="use_ollama",
    help="켜면 로컬 Ollama 서버를 사용합니다.",
)

ai_provider = "ollama" if st.session_state.use_ollama else "openai"
if ai_provider == "ollama":
    model = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
    client = OpenAI(
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )
else:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI()

# ==========================================
# 2. 세션 상태 (Session State) 초기화
# ==========================================
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "당신은 IT 운영 및 웹 리서치 보조 에이전트입니다. 사용자의 요청을 파악해 필요한 작업을 수행하고 결과를 명확하게 안내하세요."}
    ]

if "cwd" not in st.session_state:
    st.session_state.cwd = Path.cwd()

if "driver" not in st.session_state:
    st.session_state.driver = None

if "browser_screenshot" not in st.session_state:
    st.session_state.browser_screenshot = None

if "auto_approve_cmd" not in st.session_state:
    st.session_state.auto_approve_cmd = True

if "scheduled_commands" not in st.session_state:
    st.session_state.scheduled_commands = []

if "work_mode" not in st.session_state:
    st.session_state.work_mode = "기본 모드"

if "consecutive_tool_failures" not in st.session_state:
    st.session_state.consecutive_tool_failures = 0

if "last_failed_tool" not in st.session_state:
    st.session_state.last_failed_tool = None

if "suppressed_help_tool" not in st.session_state:
    st.session_state.suppressed_help_tool = None

if "help_offer" not in st.session_state:
    st.session_state.help_offer = None

if "allow_python_run" not in st.session_state:
    st.session_state.allow_python_run = False

if "vscode_monitor_file" not in st.session_state:
    st.session_state.vscode_monitor_file = ""

if "live_analysis_token" not in st.session_state:
    st.session_state.live_analysis_token = secrets.token_urlsafe(32)

if "python_syntax_signature" not in st.session_state:
    st.session_state.python_syntax_signature = None

if "python_syntax_checks" not in st.session_state:
    st.session_state.python_syntax_checks = 0

if "python_syntax_last_check" not in st.session_state:
    st.session_state.python_syntax_last_check = 0.0

if "python_syntax_offer" not in st.session_state:
    st.session_state.python_syntax_offer = None

if "python_syntax_dismissed_signature" not in st.session_state:
    st.session_state.python_syntax_dismissed_signature = None

# ==========================================
# 3. Chrome 브라우저 드라이버 관리 (지연 초기화)
# ==========================================
def get_driver():
    if st.session_state.driver is None:
        st.toast("🌐 웹 도구가 호출되어 Chrome 브라우저를 시작합니다...", icon="🌐")
        chrome_options = webdriver.ChromeOptions()
        # 백그라운드 실행을 원하면 주석 해제
        # chrome_options.add_argument('--headless')
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
        driver.implicitly_wait(5)
        st.session_state.driver = driver
    return st.session_state.driver

def capture_browser_screen():
    """현재 Chrome 화면을 Streamlit 미리보기용으로 저장합니다."""
    browser = get_driver()
    st.session_state.browser_screenshot = browser.get_screenshot_as_png()
    return st.session_state.browser_screenshot

# ==========================================
# 4. 에이전트 도구 (Tool Functions) 정의
# ==========================================
def execute_cmd(command: str) -> str:
    if not st.session_state.auto_approve_cmd:
        return "사용자 설정에 의해 CMD 명령어 실행 승인이 거부되었습니다. (사이드바에서 자동 승인을 켜주세요)"

    encoding = 'cp949' if platform.system() == 'Windows' else 'utf-8'
    stripped_command = command.strip()
    
    # cd 커스텀 처리 (디렉토리 상태 유지)
    if stripped_command.lower() == 'cd' or stripped_command.lower().startswith('cd '):
        target = stripped_command[2:].strip()
        if target.lower().startswith('/d '):
            target = target[3:].strip()
        if not target:
            return f"현재 디렉토리: {st.session_state.cwd}"
        target = target.strip('"')
        target_path = Path(target)
        if not target_path.is_absolute():
            target_path = st.session_state.cwd / target_path
        target_path = target_path.resolve()
        if not target_path.is_dir():
            return f"디렉토리를 찾을 수 없습니다: {target_path}"
        st.session_state.cwd = target_path
        return f"현재 디렉토리: {st.session_state.cwd}"
    
    try:
        result = subprocess.run(
            command, shell=True, cwd=st.session_state.cwd, check=True, 
            capture_output=True, text=True, encoding=encoding
        )
        return f"실행 성공:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"실행 실패 (코드 {e.returncode}):\n{e.stderr}"
    except Exception as e:
        return f"시스템 오류:\n{str(e)}"

def resolve_workspace_path(path: str) -> Path:
    """작업 폴더 내부의 경로만 반환합니다."""
    workspace = Path(st.session_state.cwd).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    try:
        target.relative_to(workspace)
    except ValueError as error:
        raise ValueError("작업 폴더 바깥의 경로는 사용할 수 없습니다.") from error
    return target

def find_vscode_executable() -> Path | None:
    launcher = shutil.which("code")
    candidates = []
    if launcher:
        launcher_path = Path(launcher)
        if launcher_path.suffix.lower() == ".exe":
            candidates.append(launcher_path)
        candidates.extend([
            launcher_path.parent.parent / "Code.exe",
            launcher_path.with_name("Code.exe"),
        ])

    for environment_variable in ("LOCALAPPDATA", "ProgramFiles"):
        install_root = os.getenv(environment_variable)
        if install_root:
            candidates.append(
                Path(install_root) / "Microsoft VS Code" / "Code.exe"
            )

    return next((candidate for candidate in candidates if candidate.is_file()), None)

def open_in_vscode(path: str = ".") -> str:
    try:
        target = resolve_workspace_path(path)
        if not target.exists():
            return f"VS Code 열기 실패: 경로가 존재하지 않습니다: {path}"
        if not target.is_file() and not target.is_dir():
            return f"VS Code 열기 실패: 파일 또는 폴더가 아닙니다: {path}"

        executable = find_vscode_executable()
        if executable is None:
            return "VS Code 실행 파일을 찾지 못했습니다. VS Code를 설치하거나 code 명령을 PATH에 추가해 주세요."

        subprocess.Popen(
            [str(executable), "--reuse-window", str(target)],
            cwd=st.session_state.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"VS Code에서 열었습니다: {target}"
    except (OSError, ValueError) as error:
        return f"VS Code 열기 실패: {error}"

class LiveAnalysisHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def register(self, token: str, workspace: Path, target_file: str):
        root = workspace.resolve()
        target = (root / target_file).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("감시 파일은 작업 폴더 안에 있어야 합니다.") from error

        with self._lock:
            current = self._sessions.get(token)
            if current and current["target"] == target:
                current["root"] = root
                return
            self._sessions[token] = {
                "root": root,
                "target": target,
                "source": None,
                "updated_at": 0.0,
            }

    def publish(self, token: str, file_path: str, source: str) -> str:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return "unauthorized"

            target = Path(file_path).resolve()
            try:
                target.relative_to(session["root"])
            except ValueError:
                return "outside_workspace"
            if target != session["target"]:
                return "not_monitored"
            session["source"] = source
            session["updated_at"] = time.time()
            return "ok"

    def get_document(self, token: str):
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session["source"] is None:
                return None
            return {
                "path": session["target"],
                "source": session["source"],
                "updated_at": session["updated_at"],
            }

    def unregister(self, token: str):
        with self._lock:
            self._sessions.pop(token, None)

def create_live_analysis_handler(hub: LiveAnalysisHub):
    class LiveAnalysisHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/live-analysis":
                self.send_error(404)
                return
            if self.headers.get("Origin"):
                self.send_error(403)
                return

            content_length = self.headers.get("Content-Length")
            if not content_length or not content_length.isdigit():
                self.send_error(400)
                return
            if int(content_length) > 1_000_000:
                self.send_error(413)
                return

            authorization = self.headers.get("Authorization", "")
            token = authorization.removeprefix("Bearer ").strip()
            if not token:
                self.send_error(401)
                return

            try:
                payload = json.loads(self.rfile.read(int(content_length)))
                file_path = payload["path"]
                source = payload["source"]
                if not isinstance(file_path, str) or not isinstance(source, str):
                    raise ValueError("path and source must be strings")
            except (json.JSONDecodeError, KeyError, ValueError):
                self.send_error(400)
                return

            outcome = hub.publish(token, file_path, source)
            if outcome == "unauthorized":
                self.send_error(401)
            elif outcome == "outside_workspace":
                self.send_error(403)
            elif outcome == "not_monitored":
                self.send_error(409)
            else:
                self.send_response(204)
                self.end_headers()

        def log_message(self, format, *args):
            return

    return LiveAnalysisHandler

class LiveAnalysisServer:
    def __init__(self):
        self.hub = LiveAnalysisHub()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_live_analysis_handler(self.hub),
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="quasar-live-analysis",
            daemon=True,
        )
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/live-analysis"

@st.cache_resource
def get_live_analysis_server():
    return LiveAnalysisServer()

def get_python_file_paths(directory: str = ".") -> list[str]:
    folder = resolve_workspace_path(directory)
    if not folder.is_dir():
        return []

    excluded_directories = {".git", ".venv", "venv", "__pycache__", "node_modules"}
    files = []
    for current, directories, filenames in os.walk(folder, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in excluded_directories
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                file_path = Path(current) / filename
                files.append(str(file_path.relative_to(st.session_state.cwd)))
                if len(files) == 100:
                    return files
    return files

def python_list_files(directory: str = ".") -> str:
    """작업 폴더 안의 Python 파일을 최대 100개까지 나열합니다."""
    try:
        folder = resolve_workspace_path(directory)
        if not folder.is_dir():
            return f"Python 파일 목록 실패: 디렉터리가 아닙니다: {directory}"
        files = get_python_file_paths(directory)
        if not files:
            return "Python 파일을 찾지 못했습니다."
        suffix = "\n(최대 100개까지만 표시)" if len(files) == 100 else ""
        return "작업 폴더의 Python 파일:\n" + "\n".join(files) + suffix
    except (OSError, ValueError) as error:
        return f"Python 파일 목록 실패: {error}"

def python_check_syntax(file_path: str) -> str:
    """Python 파일을 실행하지 않고 문법을 검사합니다."""
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 문법 검사 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        with tokenize.open(source_path) as source_file:
            source = source_file.read()
        ast.parse(source, filename=str(source_path))
        return f"Python 문법 검사 통과: {source_path.name}"
    except SyntaxError as error:
        return (
            f"Python 문법 오류: {file_path}:{error.lineno}:{error.offset} "
            f"{error.msg}"
        )
    except (OSError, UnicodeError, ValueError) as error:
        return f"Python 문법 검사 실패: {error}"

def python_read_file(file_path: str) -> str:
    """Quasar가 작업 폴더 안의 Python 파일을 검토할 수 있도록 내용을 읽습니다."""
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 파일 읽기 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        with tokenize.open(source_path) as source_file:
            source = source_file.read()
        if len(source) > 12000:
            source = source[:12000] + "\n... (파일 내용 일부 생략)"
        return f"파일: {source_path.name}\n```python\n{source}\n```"
    except (OSError, UnicodeError, ValueError) as error:
        return f"Python 파일 읽기 실패: {error}"

def python_run_file(file_path: str) -> str:
    """사용자 승인 설정이 켜진 경우 작업 폴더 안의 Python 파일을 실행합니다."""
    if not st.session_state.allow_python_run:
        return "Python 실행이 비활성화되어 있습니다. 사이드바에서 Python 파일 실행을 허용해 주세요."
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 실행 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        result = subprocess.run(
            [sys.executable, str(source_path)],
            cwd=st.session_state.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        output = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        if len(output) > 6000:
            output = output[:6000] + "\n... (출력 일부 생략)"
        if result.returncode:
            return f"Python 실행 실패 (코드 {result.returncode}):\n{output or '출력 없음'}"
        return f"Python 실행 완료:\n{output or '출력 없음'}"
    except subprocess.TimeoutExpired as error:
        output = error.stdout or error.stderr or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return f"Python 실행 실패: 30초 시간 제한을 초과했습니다.\n{output[:6000]}"
    except (OSError, ValueError) as error:
        return f"Python 실행 실패: {error}"

def web_search(query: str) -> str:
    try:
        browser = get_driver()
        browser.get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
        time.sleep(2)
        results = browser.find_elements(By.CSS_SELECTOR, "div.result")
        output = []
        for res in results[:5]:
            try:
                title = res.find_element(By.TAG_NAME, "h3").text
                link = res.find_element(By.CSS_SELECTOR, "a.result__a").get_attribute("href")
                snippet = res.find_element(By.CSS_SELECTOR, ".result__snippet").text
                output.append(f"제목: {title}\n링크: {link}\n내용 요약: {snippet}\n---")
            except:
                continue
        capture_browser_screen()
        if output:
            return "\n".join(output)
        body_text = browser.find_element(By.TAG_NAME, "body").text
        return f"검색 결과 본문:\n{body_text[:4000]}"
    except Exception as e:
        return f"검색 중 오류 발생: {str(e)}"

def web_navigate(url: str) -> str:
    try:
        browser = get_driver()
        browser.get(url)
        time.sleep(2)
        body_text = browser.find_element(By.TAG_NAME, "body").text
        capture_browser_screen()
        return f"페이지 로드 성공. 본문 내용(일부):\n{body_text[:3000]}"
    except Exception as e:
        return f"페이지 이동 중 오류 발생: {str(e)}"

def web_click(css_selector: str) -> str:
    try:
        browser = get_driver()
        element = WebDriverWait(browser, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector))
        )
        element.click()
        time.sleep(2)
        return f"요소 클릭 성공. 현재 URL: {browser.current_url}"
    except Exception as e:
        return f"요소 클릭 실패: {str(e)}"

def web_input(css_selector: str, text: str, press_enter: bool = False) -> str:
    """현재 페이지의 입력 요소에 텍스트를 입력하고 필요하면 Enter를 누릅니다."""
    try:
        browser = get_driver()
        try:
            element = WebDriverWait(browser, 5).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, css_selector))
            )
        except Exception:
            element = browser.switch_to.active_element
            if element.tag_name not in {"input", "textarea"}:
                raise
        element.clear()
        element.send_keys(text)
        if press_enter:
            from selenium.webdriver.common.keys import Keys
            element.send_keys(Keys.ENTER)
            time.sleep(2)
        capture_browser_screen()
        return f"입력 성공: {css_selector} (현재 URL: {browser.current_url})"
    except Exception as e:
        return f"텍스트 입력 실패: {str(e)}"

def web_scroll(direction: str = "down") -> str:
    """현재 페이지를 위나 아래로 스크롤하고 본문 일부를 반환합니다."""
    try:
        browser = get_driver()
        amount = "window.innerHeight * 0.8"
        offset = amount if direction.lower() == "down" else f"-({amount})"
        browser.execute_script(f"window.scrollBy(0, {offset});")
        time.sleep(0.5)
        body_text = browser.find_element(By.TAG_NAME, "body").text
        capture_browser_screen()
        return f"스크롤 성공 ({direction}). 본문 내용(일부):\n{body_text[:3000]}"
    except Exception as e:
        return f"스크롤 실패: {str(e)}"

def web_back() -> str:
    """브라우저를 이전 페이지로 이동합니다."""
    try:
        browser = get_driver()
        browser.back()
        time.sleep(1)
        capture_browser_screen()
        return f"이전 페이지로 이동했습니다. 현재 URL: {browser.current_url}"
    except Exception as e:
        return f"뒤로가기 실패: {str(e)}"

def web_key_press(key: str, text: str = "") -> str:
    """현재 포커스된 웹 요소에 키 또는 텍스트를 입력합니다."""
    try:
        browser = get_driver()
        actions = ActionChains(browser)
        if text:
            actions.send_keys(text)
        if key:
            key_value = getattr(Keys, key.upper(), key)
            actions.send_keys(key_value)
        actions.perform()
        capture_browser_screen()
        return f"키보드 입력 성공: {text}{key}"
    except Exception as e:
        return f"키보드 입력 실패: {str(e)}"

def gmail_open() -> str:
    """로그인된 Chrome 세션에서 Gmail을 엽니다."""
    try:
        browser = get_driver()
        browser.get("https://mail.google.com/mail/u/0/#inbox")
        time.sleep(3)
        return f"Gmail을 열었습니다. 현재 URL: {browser.current_url}"
    except Exception as e:
        return f"Gmail 열기 실패: {str(e)}"

def gmail_search(query: str) -> str:
    """Gmail 검색창에서 메일을 검색하고 화면 텍스트를 반환합니다."""
    try:
        browser = get_driver()
        if "mail.google.com" not in browser.current_url:
            gmail_open()
        search_box = WebDriverWait(browser, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[placeholder='Search mail'], input[aria-label='Search mail']"))
        )
        search_box.clear()
        search_box.send_keys(query, Keys.ENTER)
        time.sleep(2)
        return f"Gmail 검색 완료: {query}\n{browser.find_element(By.TAG_NAME, 'body').text[:4000]}"
    except Exception as e:
        return f"Gmail 검색 실패: {str(e)}"

def gmail_create_draft(to: str, subject: str, body: str) -> str:
    """Gmail에서 메일을 작성하고 전송하지 않은 채 임시저장합니다."""
    try:
        browser = get_driver()
        if "mail.google.com" not in browser.current_url:
            gmail_open()
        compose = WebDriverWait(browser, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[self::div or self::button][@role='button' and (contains(@aria-label, 'Compose') or contains(@aria-label, '편지쓰기'))]"))
        )
        compose.click()
        to_box = WebDriverWait(browser, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "input[aria-label='To recipients'], input[peoplekit-id='BbVjBd']"))
        )
        to_box.send_keys(to, Keys.ENTER)
        subject_box = browser.find_element(By.NAME, "subjectbox")
        subject_box.send_keys(subject)
        body_box = browser.find_element(By.CSS_SELECTOR, "div[aria-label='Message Body'], div[aria-label='메시지 본문']")
        body_box.send_keys(body)
        body_box.send_keys(Keys.ESCAPE)
        time.sleep(1)
        return f"Gmail 임시저장 완료: 받는 사람={to}, 제목={subject} (메일은 전송하지 않았습니다.)"
    except Exception as e:
        return f"Gmail 임시저장 실패: {str(e)}"

def get_current_time() -> str:
    now = datetime.now()
    return f"현재 시간은 {now.strftime('%Y년 %m월 %d일 %H시 %M분 %S초')} 입니다."

def create_calendar_event(title: str, date: str, time: str, description: str = "") -> str:
    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//My Agent//EN
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART:{date}T{time}
DTEND:{date}T{time}
END:VEVENT
END:VCALENDAR"""
    try:
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"event_{date}.ics")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(ics_content)
        
        if platform.system() == 'Windows':
            os.startfile(file_path)
        elif platform.system() == 'Darwin':
            subprocess.run(['open', file_path])
        else:
            subprocess.run(['xdg-open', file_path])
        return f"일정 파일이 생성되어 캘린더 앱을 열었습니다. (파일 경로: {file_path})"
    except Exception as e:
        return f"캘린더 연결 중 오류 발생: {str(e)}"

def _trigger_alarm(message: str):
    for _ in range(3):
        if platform.system() == "Windows":
            winsound.Beep(1000, 500)
        else:
            print("\a", end="", flush=True)
        time.sleep(0.2)
    print(f"\n🔔 [알람/타이머 종료!] {message}")

def set_timer(seconds: int, message: str) -> str:
    timer = threading.Timer(seconds, _trigger_alarm, args=[message])
    timer.daemon = True
    timer.start()
    return f"{seconds}초 뒤에 '{message}' 내용으로 알림이 울리도록 타이머를 설정했습니다."

def set_alarm(target_time: str, message: str) -> str:
    try:
        now = datetime.now()
        target_hour, target_minute = map(int, target_time.split(':'))
        target_dt = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += timedelta(days=1)
        delta_seconds = (target_dt - now).total_seconds()
        timer = threading.Timer(delta_seconds, _trigger_alarm, args=[message])
        timer.daemon = True
        timer.start()
        return f"{target_time}({int(delta_seconds)}초 뒤)에 '{message}' 알림이 울리도록 설정했습니다."
    except Exception as e:
        return f"알람 설정 실패: {str(e)}"

def _run_scheduled_command(command: str, working_directory: Path, job: dict):
    """예약 시점에 저장한 경로에서 명령을 실행합니다."""
    encoding = 'cp949' if platform.system() == 'Windows' else 'utf-8'
    try:
        result = subprocess.run(
            command, shell=True, cwd=working_directory, check=True,
            capture_output=True, text=True, encoding=encoding
        )
        job["status"] = f"완료: {result.stdout.strip() or '출력 없음'}"
    except subprocess.CalledProcessError as error:
        job["status"] = f"실패 (코드 {error.returncode}): {error.stderr.strip()}"
    except Exception as error:
        job["status"] = f"오류: {error}"
    _trigger_alarm(f"예약 명령어 완료: {command}")

def schedule_command(target_datetime: str, command: str) -> str:
    """지정한 YYYY-MM-DD HH:MM 시각에 명령어를 한 번 실행합니다."""
    if not st.session_state.auto_approve_cmd:
        return "예약 명령어는 백그라운드에서 실행되므로 CMD 자동 승인을 먼저 켜주세요."
    try:
        target = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M")
        delay = (target - datetime.now()).total_seconds()
        if delay <= 0:
            return "예약 시각은 현재 시각보다 미래여야 합니다."
        job = {
            "target": target.strftime("%Y-%m-%d %H:%M"),
            "command": command,
            "status": "대기 중",
        }
        st.session_state.scheduled_commands.append(job)
        timer = threading.Timer(
            delay, _run_scheduled_command,
            args=[command, st.session_state.cwd, job]
        )
        timer.daemon = True
        timer.start()
        return f"{target_datetime}에 '{command}' 명령어를 예약했습니다."
    except ValueError:
        return "예약 실패: 날짜 형식은 YYYY-MM-DD HH:MM이어야 합니다."

# ==========================================
# 5. Tools 명세서
# ==========================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_cmd",
            "description": "시스템의 명령 프롬프트(CMD)에서 명령어를 실행합니다.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "실행할 시스템 명령어"}},
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "구글 웹 검색을 수행하고 상위 결과들의 제목, 링크, 요약을 반환합니다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "검색할 키워드"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_navigate",
            "description": "특정 URL 웹페이지로 이동하여 본문 텍스트를 읽어옵니다.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "이동할 웹페이지 URL"}},
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_click",
            "description": "현재 웹페이지에서 CSS 선택자를 사용해 버튼이나 링크를 클릭합니다.",
            "parameters": {
                "type": "object",
                "properties": {"css_selector": {"type": "string", "description": "클릭할 요소의 CSS 선택자"}},
                "required": ["css_selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_input",
            "description": "현재 웹페이지의 입력 요소에 텍스트를 입력하고 필요하면 Enter를 누릅니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "css_selector": {"type": "string", "description": "입력할 요소의 CSS 선택자"},
                    "text": {"type": "string", "description": "입력할 텍스트"},
                    "press_enter": {"type": "boolean", "description": "입력 후 Enter를 누를지 여부"}
                },
                "required": ["css_selector", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_scroll",
            "description": "현재 웹페이지를 위나 아래로 스크롤하고 본문 일부를 반환합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"], "description": "스크롤 방향"}
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_back",
            "description": "Chrome을 이전 페이지로 이동합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_key_press",
            "description": "현재 포커스된 웹 요소에 텍스트와 키를 입력합니다. key 예: ENTER, TAB, ESCAPE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "누를 키 이름"},
                    "text": {"type": "string", "description": "함께 입력할 텍스트"}
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_open",
            "description": "로그인된 Chrome 세션에서 Gmail 받은편지함을 엽니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search",
            "description": "Gmail에서 검색어로 메일을 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Gmail 검색어"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_create_draft",
            "description": "Gmail 메일을 작성해 임시저장합니다. 메일은 전송하지 않습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "받는 사람 이메일"},
                    "subject": {"type": "string", "description": "메일 제목"},
                    "body": {"type": "string", "description": "메일 본문"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "현재 날짜와 시간을 확인합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "새로운 일정을 캘린더 앱에 추가하기 위해 .ics 파일을 생성하고 실행합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "일정 제목"},
                    "date": {"type": "string", "description": "일정 날짜 (YYYYMMDD 형식)"},
                    "time": {"type": "string", "description": "일정 시간 (HHMM00 형식)"},
                    "description": {"type": "string", "description": "상세 설명"}
                },
                "required": ["title", "date", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_command",
            "description": "지정한 미래 시각에 현재 작업 경로에서 명령어를 한 번 실행합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_datetime": {"type": "string", "description": "예약 시각 (YYYY-MM-DD HH:MM)"},
                    "command": {"type": "string", "description": "실행할 명령어"}
                },
                "required": ["target_datetime", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "n초 뒤에 알려주는 타이머 기능입니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "기다릴 시간(초 단위)"},
                    "message": {"type": "string", "description": "알림 메시지"}
                },
                "required": ["seconds", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": "특정 시각(HH:MM)에 알려주는 알람 기능입니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_time": {"type": "string", "description": "알람 시각 (HH:MM)"},
                    "message": {"type": "string", "description": "알림 메시지"}
                },
                "required": ["target_time", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vscode_open",
            "description": "작업 폴더의 파일 또는 폴더를 VS Code에서 엽니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "열 파일 또는 폴더의 작업 폴더 상대 경로",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_read_file",
            "description": "작업 폴더 안의 Python 파일 내용을 읽어 코드 검토를 돕습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "읽을 .py 파일의 작업 폴더 상대 경로",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_list_files",
            "description": "작업 폴더 안의 Python 파일을 최대 100개까지 나열합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "검색할 작업 폴더 상대 경로 (기본값: .)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_check_syntax",
            "description": "작업 폴더 안의 Python 파일을 실행하지 않고 문법 검사합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "검사할 .py 파일의 작업 폴더 상대 경로",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_run_file",
            "description": "사용자가 허용한 경우 작업 폴더 안의 Python 파일을 실행합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "실행할 .py 파일의 작업 폴더 상대 경로",
                    }
                },
                "required": ["file_path"],
            },
        },
    }
]

tool_catalog = [
    ("execute_cmd", "작업 폴더에서 시스템 명령을 실행합니다."),
    ("web_search", "웹에서 검색어를 찾아 결과를 요약합니다."),
    ("web_navigate", "URL로 이동해 페이지 본문을 읽습니다."),
    ("web_click", "현재 웹 페이지의 CSS 선택자 요소를 클릭합니다."),
    ("web_input", "웹 페이지 입력란에 텍스트를 입력합니다."),
    ("web_scroll", "현재 페이지를 위나 아래로 스크롤합니다."),
    ("web_back", "브라우저를 이전 페이지로 이동합니다."),
    ("web_key_press", "현재 포커스된 요소에 키나 텍스트를 입력합니다."),
    ("gmail_open", "로그인된 브라우저에서 Gmail 받은편지함을 엽니다."),
    ("gmail_search", "Gmail에서 메일을 검색합니다."),
    ("gmail_create_draft", "Gmail 메일을 전송하지 않고 임시저장합니다."),
    ("get_current_time", "현재 날짜와 시간을 확인합니다."),
    ("create_calendar_event", "캘린더 앱에서 열 수 있는 일정 파일을 만듭니다."),
    ("schedule_command", "미래 시각에 시스템 명령을 예약 실행합니다."),
    ("set_timer", "지정한 초 뒤에 알림을 울립니다."),
    ("set_alarm", "지정한 시각에 알림을 울립니다."),
    ("vscode_open", "작업 폴더의 파일 또는 폴더를 VS Code에서 엽니다."),
    ("python_read_file", "저장된 Python 코드를 Quasar가 읽고 검토합니다."),
    ("python_list_files", "작업 폴더의 Python 파일 목록을 확인합니다."),
    ("python_check_syntax", "Python 파일을 실행하지 않고 문법을 검사합니다."),
    ("python_run_file", "허용 설정 후 작업 폴더의 Python 파일을 실행합니다."),
]

tool_schemas = {
    tool["function"]["name"]: tool
    for tool in tools
}
tool_catalog_text = "\n".join(
    f"- {name}: {description}"
    for name, description in tool_catalog
)
tool_selector = {
    "type": "function",
    "function": {
        "name": "select_tool",
        "description": (
            "사용자 요청에 필요한 도구를 하나 선택합니다. "
            "도구 목록과 간단한 설명:\n"
            f"{tool_catalog_text}\n"
            "도구가 필요하지 않으면 none을 선택합니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "enum": ["none", *tool_schemas.keys()],
                    "description": "사용할 도구 이름 또는 none",
                }
            },
            "required": ["tool_name"],
        },
    },
}

# ==========================================
# 6. 음성 인식 및 에이전트 루프 처리 함수
# ==========================================
def listen_voice():
    """로컬 마이크를 통해 한국어 음성을 텍스트로 인식합니다."""
    if not HAS_SR:
        st.error("SpeechRecognition 패키지가 설치되지 않았습니다. (`pip install SpeechRecognition pyaudio`)")
        return None
    
    r = sr.Recognizer()
    with sr.Microphone() as source:
        st.toast("🎙️ 음성을 듣고 있습니다... 말씀하세요!", icon="🎙️")
        r.adjust_for_ambient_noise(source, duration=0.8)
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=10)
            text = r.recognize_google(audio, language="ko-KR")
            return text
        except sr.WaitTimeoutError:
            st.warning("음성이 감지되지 않았습니다.")
        except sr.UnknownValueError:
            st.warning("음성을 이해하지 못했습니다.")
        except Exception as e:
            st.error(f"음성 인식 오류: {str(e)}")
    return None

def run_agent_tool(tool_name: str, function_args: dict) -> str:
    """AI 도구 하나를 실행하고 오류를 결과로 변환합니다."""
    try:
        if tool_name == "execute_cmd":
            return execute_cmd(function_args.get("command", ""))
        if tool_name == "web_search":
            return web_search(function_args.get("query", ""))
        if tool_name == "web_navigate":
            return web_navigate(function_args.get("url", ""))
        if tool_name == "web_click":
            return web_click(function_args.get("css_selector", ""))
        if tool_name == "web_input":
            return web_input(function_args.get("css_selector", ""), function_args.get("text", ""), function_args.get("press_enter", False))
        if tool_name == "web_scroll":
            return web_scroll(function_args.get("direction", "down"))
        if tool_name == "web_back":
            return web_back()
        if tool_name == "web_key_press":
            return web_key_press(function_args.get("key", ""), function_args.get("text", ""))
        if tool_name == "gmail_open":
            return gmail_open()
        if tool_name == "gmail_search":
            return gmail_search(function_args.get("query", ""))
        if tool_name == "gmail_create_draft":
            return gmail_create_draft(function_args.get("to", ""), function_args.get("subject", ""), function_args.get("body", ""))
        if tool_name == "get_current_time":
            return get_current_time()
        if tool_name == "create_calendar_event":
            return create_calendar_event(function_args.get("title", ""), function_args.get("date", ""), function_args.get("time", ""), function_args.get("description", ""))
        if tool_name == "set_timer":
            return set_timer(function_args.get("seconds", 0), function_args.get("message", ""))
        if tool_name == "set_alarm":
            return set_alarm(function_args.get("target_time", ""), function_args.get("message", ""))
        if tool_name == "schedule_command":
            return schedule_command(function_args.get("target_datetime", ""), function_args.get("command", ""))
        if tool_name == "vscode_open":
            return open_in_vscode(function_args.get("path", "."))
        if tool_name == "python_read_file":
            return python_read_file(function_args.get("file_path", ""))
        if tool_name == "python_list_files":
            return python_list_files(function_args.get("directory", "."))
        if tool_name == "python_check_syntax":
            return python_check_syntax(function_args.get("file_path", ""))
        if tool_name == "python_run_file":
            return python_run_file(function_args.get("file_path", ""))
        return f"알 수 없는 도구: {tool_name}"
    except Exception as error:
        return f"도구 실행 오류: {error}"

def record_tool_outcome(tool_name: str, result: str):
    """공동작업 모드에서 연속 도구 실패만 세션 내 도움 후보로 기록합니다."""
    if st.session_state.work_mode != "공동작업 모드":
        return

    failure_prefixes = (
        "실행 실패",
        "시스템 오류",
        "검색 중 오류",
        "페이지 이동 중 오류",
        "요소 클릭 실패",
        "텍스트 입력 실패",
        "스크롤 실패",
        "뒤로가기 실패",
        "키보드 입력 실패",
        "Gmail 열기 실패",
        "Gmail 검색 실패",
        "Gmail 임시저장 실패",
        "캘린더 연결 중 오류",
        "알람 설정 실패",
        "예약 실패",
        "도구 실행 오류",
        "도구 인자 해석 오류",
        "CMD 명령어 실행 승인이 거부",
        "Python 파일 목록 실패",
        "Python 파일 읽기 실패",
        "Python 문법 오류",
        "Python 문법 검사 실패",
        "Python 실행 실패",
        "VS Code 열기 실패",
    )
    failed = result.startswith(failure_prefixes)

    if not failed:
        if st.session_state.last_failed_tool == tool_name:
            st.session_state.suppressed_help_tool = None
        st.session_state.last_failed_tool = None
        st.session_state.consecutive_tool_failures = 0
        return

    if st.session_state.last_failed_tool == tool_name:
        st.session_state.consecutive_tool_failures += 1
    else:
        st.session_state.last_failed_tool = tool_name
        st.session_state.consecutive_tool_failures = 1

    if (
        st.session_state.consecutive_tool_failures >= 2
        and st.session_state.suppressed_help_tool != tool_name
        and st.session_state.help_offer is None
    ):
        st.session_state.help_offer = {
            "tool_name": tool_name,
            "result": result[:1200],
        }

def process_agent_request(user_prompt: str):
    """도구 선택과 실행을 요청 안에서 처리하고 대화 메모리에는 최종 답만 남깁니다."""
    persistent_messages = [
        {
            "role": "system",
            "content": (
                "당신은 IT 운영 및 웹 리서치 보조 에이전트입니다. "
                "사용자의 요청을 파악해 필요한 작업을 수행하고 결과를 명확하게 안내하세요."
            ),
        }
    ]
    for message in st.session_state.messages:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if role in {"user", "assistant"} and isinstance(content, str) and content:
            persistent_messages.append({"role": role, "content": content})

    st.session_state.messages = persistent_messages
    working_messages = [*persistent_messages, {"role": "user", "content": user_prompt}]

    def save_final_answer(answer: str):
        st.session_state.messages.extend([
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": answer},
        ])

    try:
        with st.spinner("🤖 에이전트 분석 및 작업 실행 중..."):
            for _ in range(6):
                selection_messages = [
                    working_messages[0],
                    {
                        "role": "system",
                        "content": (
                            "사용자 요청과 대화 맥락을 보고 도구가 필요한지 판단하세요. "
                            "필요하면 목록에서 가장 적합한 도구 하나를 선택하고, "
                            "필요하지 않으면 도구 없이 바로 답하세요."
                        ),
                    },
                    *working_messages[1:],
                ]
                selection_response = client.chat.completions.create(
                    model=model,
                    messages=selection_messages,
                    tools=[tool_selector],
                    tool_choice="auto",
                )
                selection_message = selection_response.choices[0].message
                if not selection_message.tool_calls:
                    answer = selection_message.content or "요청을 처리했습니다."
                    save_final_answer(answer)
                    return

                selection_call = selection_message.tool_calls[0]
                try:
                    selected_name = json.loads(
                        selection_call.function.arguments or "{}"
                    ).get("tool_name")
                except (json.JSONDecodeError, AttributeError) as error:
                    raise ValueError(f"도구 선택 결과를 해석할 수 없습니다: {error}") from error

                if selected_name == "none":
                    answer_messages = [
                        working_messages[0],
                        {"role": "system", "content": "도구 없이 사용자에게 답하세요."},
                        *working_messages[1:],
                    ]
                    answer_response = client.chat.completions.create(
                        model=model,
                        messages=answer_messages,
                    )
                    answer = answer_response.choices[0].message.content or "요청을 처리했습니다."
                    save_final_answer(answer)
                    return

                selected_tool = tool_schemas.get(selected_name)
                if selected_tool is None:
                    raise ValueError(f"알 수 없는 도구가 선택되었습니다: {selected_name}")

                tool_messages = [
                    working_messages[0],
                    {
                        "role": "system",
                        "content": f"선택된 도구 `{selected_name}`를 사용해 요청을 처리하세요.",
                    },
                    *working_messages[1:],
                ]
                tool_response = client.chat.completions.create(
                    model=model,
                    messages=tool_messages,
                    tools=[selected_tool],
                    tool_choice="auto",
                )
                tool_message = tool_response.choices[0].message
                if not tool_message.tool_calls:
                    answer = tool_message.content or "요청을 처리했습니다."
                    save_final_answer(answer)
                    return

                working_messages.append(tool_message)
                for tool_call in tool_message.tool_calls:
                    if tool_call.function.name != selected_name:
                        raise ValueError(
                            f"선택하지 않은 도구가 호출되었습니다: {tool_call.function.name}"
                        )
                    try:
                        function_args = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError as error:
                        result = f"도구 인자 해석 오류: {error}"
                    else:
                        result = run_agent_tool(tool_call.function.name, function_args)
                    record_tool_outcome(tool_call.function.name, result)
                    st.toast(f"🛠️ 도구 실행 중: `{tool_call.function.name}`", icon="⚙️")
                    working_messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": tool_call.function.name,
                        "content": result,
                    })

            answer = "작업 단계가 너무 길어 잠시 멈췄습니다. 현재까지의 결과를 확인해 주세요."
            save_final_answer(answer)
    except Exception as error:
        error_message = f"작업 중 일시적인 오류가 발생했지만 앱은 계속 실행됩니다: {error}"
        save_final_answer(error_message)
        st.error(error_message)

@st.fragment(run_every="1s")
def render_python_syntax_monitor():
    if st.session_state.work_mode != "공동작업 모드" or not st.session_state.vscode_monitor_file:
        st.session_state.python_syntax_signature = None
        st.session_state.python_syntax_checks = 0
        st.session_state.python_syntax_last_check = 0.0
        st.session_state.python_syntax_offer = None
        st.session_state.python_syntax_dismissed_signature = None
        return

    analysis_server = get_live_analysis_server()
    document = analysis_server.hub.get_document(st.session_state.live_analysis_token)
    if document is None:
        st.info("VS Code 확장에서 Quasar에 연결하면 저장 전 코드도 실시간 분석합니다.")
        return

    file_path = st.session_state.vscode_monitor_file
    try:
        monitored_path = resolve_workspace_path(file_path)
    except ValueError as error:
        st.error(f"감시 경로가 올바르지 않습니다: {error}")
        return
    if document["path"] != monitored_path:
        st.info(f"VS Code에서 `{file_path}` 파일을 활성화하면 실시간 분석을 시작합니다.")
        return
    source = document["source"]
    st.caption("VS Code 저장 전 편집 내용을 실시간 수신 중")

    try:
        ast.parse(source, filename=file_path)
    except SyntaxError as error:
        signature = (file_path, error.lineno, error.offset, error.msg)
        now = time.monotonic()
        if signature != st.session_state.python_syntax_signature:
            st.session_state.python_syntax_signature = signature
            st.session_state.python_syntax_checks = 1
            st.session_state.python_syntax_last_check = now
            st.session_state.python_syntax_offer = None
            st.session_state.python_syntax_dismissed_signature = None
        elif now - st.session_state.python_syntax_last_check >= 9:
            st.session_state.python_syntax_checks += 1
            st.session_state.python_syntax_last_check = now

        check_count = st.session_state.python_syntax_checks
        st.error(
            f"문법 오류 감지: {error.lineno}행 {error.offset}열 - {error.msg} "
            f"(10초 간격 확인 {check_count}회)"
        )
        if (
            check_count >= 3
            and st.session_state.python_syntax_dismissed_signature != signature
        ):
            st.session_state.python_syntax_offer = {
                "file_path": file_path,
                "signature": signature,
                "line": error.lineno,
                "message": error.msg,
            }
    else:
        st.session_state.python_syntax_signature = None
        st.session_state.python_syntax_checks = 0
        st.session_state.python_syntax_last_check = time.monotonic()
        st.session_state.python_syntax_offer = None
        st.session_state.python_syntax_dismissed_signature = None
        st.success("자동 문법 검사: 오류 없음")

    offer = st.session_state.python_syntax_offer
    if offer and offer["file_path"] == file_path:
        st.info(
            f"`{file_path}`의 같은 문법 오류가 10초 간격으로 "
            f"{st.session_state.python_syntax_checks}회 확인됐어요. "
            "원인과 수정 방법을 같이 살펴볼까요?"
        )
        help_col, dismiss_col = st.columns(2)
        with help_col:
            if st.button("에이전트에게 물어보기", key="python_syntax_ask_agent"):
                st.session_state.python_syntax_offer = None
                current_document = analysis_server.hub.get_document(
                    st.session_state.live_analysis_token
                )
                current_source = (
                    current_document["source"]
                    if current_document and current_document["path"] == monitored_path
                    else source
                )
                process_agent_request(
                    f"VS Code 편집 중 문법 오류가 반복 확인되어 사용자가 도움을 요청했어. "
                    f"파일: {offer['file_path']}, "
                    f"오류: {offer['line']}행 - {offer['message']}. "
                    "코드를 직접 수정하거나 실행하지 말고, 원인과 수정 예시만 설명해줘.\n\n"
                    f"현재 VS Code 편집 내용:\n```python\n{current_source[:8000]}\n```"
                )
                st.rerun()
        with dismiss_col:
            if st.button("나중에", key="python_syntax_dismiss"):
                st.session_state.python_syntax_dismissed_signature = offer["signature"]
                st.session_state.python_syntax_offer = None

# ==========================================
# 7. UI 화면 구성 (Streamlit Layout)
# ==========================================

# 사이드바
with st.sidebar:
    st.title("COMMAND CENTER")
    st.caption(f"**공급자:** `{ai_provider.upper()}` | **모델:** `{model}`")

    previous_work_mode = st.session_state.work_mode
    st.radio(
        "작업 모드",
        ["기본 모드", "공동작업 모드"],
        key="work_mode",
    )
    if st.session_state.work_mode != previous_work_mode:
        st.session_state.consecutive_tool_failures = 0
        st.session_state.last_failed_tool = None
        st.session_state.suppressed_help_tool = None
        st.session_state.help_offer = None
    if st.session_state.work_mode == "공동작업 모드":
        st.caption("앱 내 도구의 연속 실패를 감지해 도움을 제안합니다. 원시 로그는 저장하지 않습니다.")
    
    st.divider()
    st.subheader("WORKSPACE")
    st.info(f"**현재 작업 경로:**\n`{st.session_state.cwd}`")

    if st.session_state.scheduled_commands:
        st.divider()
        st.subheader("SCHEDULED COMMANDS")
        for job in st.session_state.scheduled_commands[-5:]:
            st.caption(f"{job['target']} · `{job['command']}`")
            st.write(job["status"])
    
    st.divider()
    st.subheader("SETTINGS")
    st.session_state.auto_approve_cmd = st.checkbox("CMD 명령어 자동 승인 실행", value=st.session_state.auto_approve_cmd)
    st.session_state.allow_python_run = st.checkbox(
        "Python 파일 실행 허용",
        value=st.session_state.allow_python_run,
        help="작업 폴더 안의 Python 파일만 실행합니다. 실행 시간은 30초로 제한됩니다.",
    )
    
    st.divider()
    if st.button("대화 초기화", type="secondary", use_container_width=True):
        st.session_state.messages = [st.session_state.messages[0]]
        st.rerun()

# 메인 대화 영역
st.title("업무 자동화 에이전트")
st.caption("무엇이든 입력하세요. 명령 실행, 웹 조작, Gmail, 일정 관리를 채팅으로 처리합니다.")

with st.expander("VS Code 연동", expanded=True):
    st.caption(
        "VS Code 확장이 저장 전 편집 내용을 Quasar로 보내고, 공동작업 모드에서 "
        "문법 오류를 실시간으로 분석합니다."
    )
    st.caption(
        "연결 순서: 확장 설치 → VS Code 창 다시 로드 → 감시할 파일 선택 및 열기 → "
        "명령 팔레트에서 `Quasar: Connect Live Analysis` 실행 → 아래 주소와 토큰 입력"
    )
    extension_package = Path(__file__).with_name("vscode_extension") / "quasar-live-analysis-0.1.0.vsix"
    extension_cli = shutil.which("code")
    if extension_package.is_file() and extension_cli:
        if st.button("VS Code 실시간 연동 확장 설치", key="install_vscode_live_extension"):
            try:
                install_result = subprocess.run(
                    [extension_cli, "--install-extension", str(extension_package), "--force"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )
                if install_result.returncode == 0:
                    st.success("VS Code 확장을 설치했습니다. VS Code 창을 다시 로드한 뒤 연결하세요.")
                else:
                    st.error(
                        "VS Code 확장 설치 실패:\n"
                        f"{install_result.stderr or install_result.stdout}"
                    )
            except (OSError, subprocess.TimeoutExpired) as error:
                st.error(f"VS Code 확장 설치 실패: {error}")
    else:
        st.warning("VS Code 연동 확장 패키지를 찾을 수 없습니다.")

    try:
        python_files = get_python_file_paths()
    except (OSError, ValueError) as error:
        python_files = []
        st.error(f"Python 파일 목록을 불러오지 못했습니다: {error}")

    workspace_col, file_col = st.columns(2)
    with workspace_col:
        if st.button("작업 폴더를 VS Code에서 열기", key="vscode_open_workspace"):
            result = open_in_vscode(".")
            if result.startswith("VS Code에서 열었습니다:"):
                st.success(result)
            else:
                st.error(result)

    selected_python_file = st.selectbox(
        "VS Code에서 열고 검사할 Python 파일",
        ["", *python_files],
        format_func=lambda value: value or "파일 선택",
        key="vscode_selected_file",
    )
    with file_col:
        open_and_monitor = st.button(
            "VS Code에서 열기 및 오류 감시",
            key="vscode_open_and_monitor",
            disabled=not selected_python_file,
        )
    if open_and_monitor:
        result = open_in_vscode(selected_python_file)
        if result.startswith("VS Code에서 열었습니다:"):
            st.session_state.vscode_monitor_file = selected_python_file
            analysis_server = get_live_analysis_server()
            analysis_server.hub.register(
                st.session_state.live_analysis_token,
                Path(st.session_state.cwd),
                selected_python_file,
            )
            st.session_state.python_syntax_signature = None
            st.session_state.python_syntax_checks = 0
            st.session_state.python_syntax_last_check = 0.0
            st.session_state.python_syntax_offer = None
            st.session_state.python_syntax_dismissed_signature = None
            st.success(result)
        else:
            st.error(result)

    if st.session_state.vscode_monitor_file:
        st.caption(f"문법 검사 대상: `{st.session_state.vscode_monitor_file}`")
        analysis_server = get_live_analysis_server()
        st.caption("VS Code 명령 팔레트에서 `Quasar: Connect Live Analysis`를 실행하세요.")
        st.code(
            f"Endpoint: {analysis_server.endpoint}\n"
            f"Token: {st.session_state.live_analysis_token}",
            language="text",
        )
        if st.button("오류 감시 중지", key="vscode_stop_monitor"):
            st.session_state.vscode_monitor_file = ""
            analysis_server.hub.unregister(st.session_state.live_analysis_token)
            st.session_state.python_syntax_offer = None
            st.session_state.python_syntax_signature = None
            st.session_state.python_syntax_checks = 0
            st.rerun()

    if not st.session_state.allow_python_run:
        st.caption("Python 파일 실행은 사이드바 설정에서 허용할 수 있습니다.")

render_python_syntax_monitor()

# 기존 대화 기록 출력
for msg in st.session_state.messages:
    # system 메시지 및 tool 내부 처리 메시지는 UI에 노출하지 않음
    if isinstance(msg, dict):
        role = msg.get("role")
        content = msg.get("content")
    else: # ChatCompletionMessage 객체인 경우
        role = msg.role
        content = msg.content

    if role in ["user", "assistant"] and content:
        with st.chat_message(role):
            st.write(content)

if (
    st.session_state.work_mode == "공동작업 모드"
    and st.session_state.help_offer is not None
):
    offer = st.session_state.help_offer
    st.info(
        f"`{offer['tool_name']}` 도구가 연속으로 실패했어요. "
        "원인 분석과 해결 방법을 같이 찾아볼까요?"
    )
    st.caption(f"최근 오류: {offer['result']}")
    accept_col, dismiss_col = st.columns(2)
    with accept_col:
        if st.button("도와줘", key="accept_help_offer", type="primary"):
            st.session_state.help_offer = None
            process_agent_request(
                "공동작업 모드에서 다음 오류가 반복됐어. 원인과 안전한 해결 방법을 설명해줘. "
                "승인받기 전에는 명령 실행이나 다른 도구 작업을 하지 마. "
                f"도구: {offer['tool_name']}; 오류: {offer['result']}"
            )
            st.rerun()
    with dismiss_col:
        if st.button("괜찮아", key="dismiss_help_offer"):
            st.session_state.suppressed_help_tool = offer["tool_name"]
            st.session_state.help_offer = None
            st.session_state.consecutive_tool_failures = 0
            st.rerun()

# 하단 입력 바 (텍스트 입력 + 클릭식 음성 입력)
col_input, col_voice = st.columns([8, 1.5])

with col_voice:
    voice_text = f2_voice(key="f2_voice")

with col_input:
    user_prompt = st.chat_input("명령어를 입력하거나 마이크 버튼을 눌러 말하세요...")

# 입력 처리 분기
if voice_text:
    process_agent_request(voice_text)
    st.rerun()

if user_prompt:
    process_agent_request(user_prompt)
    st.rerun()