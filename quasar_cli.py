'''
Copyright (c) 2026 readonlymemory1
All rights reserved.

'''

import argparse
import ast
import base64
import hashlib
import hmac
import json
import math
import os
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import queue
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from dotenv import load_dotenv
from openai import OpenAI
from PIL import ImageGrab
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from todo_store import todo_store

if platform.system() == "Windows":
    import winsound

load_dotenv(Path(__file__).with_name(".env"))

current_directory = Path.cwd().resolve()
browser = None
chrome_process: subprocess.Popen[bytes] | None = None
scheduled_jobs: list[dict[str, str]] = []
auto_approve_commands = True
allow_python_run = False
debug_mode = False
RAG_INDEX_FILENAME = "rag_index.json"
RAG_SUPPORTED_SUFFIXES = {".csv", ".html", ".htm", ".json", ".log", ".md", ".py", ".rst", ".toml", ".txt", ".xml", ".yaml", ".yml"}
RAG_EXCLUDED_DIRECTORIES = {".git", ".quasar", ".venv", "venv", "__pycache__", "node_modules"}
RAG_MAX_FILES = 500
RAG_MAX_FILE_SIZE = 1_000_000
RAG_MAX_CHUNKS = 10_000
RAG_CHUNK_SIZE = 1200
RAG_CHUNK_OVERLAP = 200
LIVE_ANALYSIS_MAX_BODY = 1_000_000
BROWSER_STARTUP_TIMEOUT_SECONDS = 10
BROWSER_POLL_INTERVAL_SECONDS = 0.1
BROWSER_ELEMENT_WAIT_SECONDS = 6
COLLABORATION_POLL_INTERVAL = 10
COLLABORATION_STABLE_SECONDS = 10
COLLABORATION_IDLE_SECONDS = 20
DEBUG_MAX_RESULT_CHARS = 2000
DEBUG_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|authorization|credential|cookie|body|source)",
    re.IGNORECASE,
)
DEBUG_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)(\bBearer\s+|\b(?:password|passwd|token|secret|api[_-]?key|authorization)\b"
    r"\s*(?:[:=]\s*|\s+)|\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)=|"
    r"--(?:password|token|secret|api[_-]?key)(?:=|\s+))([^\s,;]+)"
)


def load_collaboration_settings(config_path: Path) -> dict[str, int]:
    defaults = {
        "poll_interval_seconds": COLLABORATION_POLL_INTERVAL,
        "stable_seconds": COLLABORATION_STABLE_SECONDS,
        "idle_seconds": COLLABORATION_IDLE_SECONDS,
    }
    if not config_path.is_file():
        return defaults

    try:
        settings = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"공동작업 설정 파일을 읽을 수 없습니다 ({config_path}): {error}") from error
    if not isinstance(settings, dict):
        raise ValueError("공동작업 설정 파일의 최상위 값은 JSON 객체여야 합니다.")

    for key, default in defaults.items():
        value = settings.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"공동작업 설정 '{key}'는 1 이상의 정수여야 합니다.")
        defaults[key] = value
    return defaults


def create_client(provider: str, model_override: str | None = None) -> tuple[OpenAI, str]:
    if provider == "ollama":
        model = model_override or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        client = OpenAI(
            api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        )
    elif provider == "openai":
        model = model_override or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        client = OpenAI()
    else:
        raise ValueError(f"지원하지 않는 AI_PROVIDER입니다: {provider}. ollama 또는 openai를 사용하세요.")
    return client, model


def choose_provider(default_provider: str) -> str:
    providers = ("ollama", "openai")
    print("\nAI 공급자를 선택하세요:")
    for index, provider in enumerate(providers, start=1):
        marker = " (기본값)" if provider == default_provider else ""
        print(f"  {index}. {provider}{marker}")
    while True:
        choice = input(f"번호 또는 공급자 이름 [{default_provider}]: ").strip().lower()
        if not choice:
            return default_provider
        if choice in providers:
            return choice
        if choice in {"1", "2"}:
            return providers[int(choice) - 1]
        print("1/2 또는 ollama/openai를 입력해 주세요.")


def list_ollama_cloud_models() -> list[str]:
    request = urllib.request.Request(
        "https://ollama.com/api/tags",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        catalog = json.load(response)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise ValueError("Ollama cloud model catalog has no models list.")

    cloud_models = {
        "gemma4:cloud",
        "gpt-oss:120b-cloud",
        "gpt-oss:20b-cloud",
    }
    for model in models:
        if not isinstance(model, dict) or model.get("size") != 0:
            continue
        name = model.get("name")
        if isinstance(name, str) and name:
            cloud_models.add(f"{name.split(':', 1)[0]}:cloud")
    return sorted(cloud_models)


def choose_model(client: OpenAI, provider: str, default_model: str) -> str:
    try:
        available_models = sorted(
            model.id
            for model in client.models.list().data
            if provider == "ollama"
            or model.id.startswith(("gpt-", "o1", "o3", "o4"))
        )
    except Exception as error:
        print(f"모델 목록 조회 실패: {error}")
        available_models = []

    cloud_models: set[str] = set()
    if provider == "ollama":
        try:
            cloud_models = set(list_ollama_cloud_models())
            available_models = sorted(set(available_models) | cloud_models)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            print(f"Ollama cloud 모델 목록 조회 실패: {error}")

    if default_model not in available_models:
        available_models.insert(0, default_model)
    if available_models:
        print(f"\n{provider.upper()} 모델을 선택하세요:")
        for index, model_name in enumerate(available_models, start=1):
            marker = " (기본값)" if model_name == default_model else ""
            if model_name in cloud_models:
                marker += " (cloud)"
            print(f"  {index}. {model_name}{marker}")
        custom_option = len(available_models) + 1
        print(f"  {custom_option}. 직접 입력")
        while True:
            choice = input(f"모델 번호 또는 이름 [{default_model}]: ").strip()
            if not choice:
                return default_model
            if choice.isdigit() and 1 <= int(choice) <= len(available_models):
                return available_models[int(choice) - 1]
            if choice == str(custom_option):
                break
            if choice in available_models:
                return choice
            if not choice.isdigit():
                return choice
            print("목록에 있는 번호 또는 모델 이름을 입력해 주세요.")
    else:
        print(f"\n{provider.upper()}에서 사용할 모델 이름을 입력하세요.")
        print("예: ollama의 qwen3:8b 또는 OpenAI의 gpt-4o-mini")
    return input(f"모델 이름 [{default_model}]: ").strip() or default_model


def execute_cmd(command: str) -> str:
    global current_directory

    stripped_command = command.strip()
    if stripped_command.lower() == "cd" or stripped_command.lower().startswith("cd "):
        target = stripped_command[2:].strip()
        if target.lower().startswith("/d "):
            target = target[3:].strip()
        if not target:
            return f"현재 디렉토리: {current_directory}"
        target_path = Path(target.strip('"'))
        if not target_path.is_absolute():
            target_path = current_directory / target_path
        target_path = target_path.resolve()
        if not target_path.is_dir():
            return f"디렉토리를 찾을 수 없습니다: {target_path}"
        current_directory = target_path
        return f"현재 디렉토리: {current_directory}"

    encoding = "cp949" if platform.system() == "Windows" else "utf-8"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=current_directory,
            check=True,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
        )
        output = result.stdout or result.stderr or "완료되었습니다."
        return f"실행 성공:\n{output}"
    except subprocess.CalledProcessError as error:
        return f"실행 실패 (코드 {error.returncode}):\n{error.stderr or error.stdout}"
    except OSError as error:
        return f"시스템 오류:\n{error}"


def resolve_workspace_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = current_directory / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(current_directory)
    except ValueError as error:
        raise ValueError("작업 폴더 바깥의 경로는 사용할 수 없습니다.") from error
    return resolved


def get_python_file_paths(directory: str = ".") -> list[str]:
    folder = resolve_workspace_path(directory)
    if not folder.is_dir():
        return []

    excluded_directories = {".git", ".venv", "venv", "__pycache__", "node_modules"}
    files = []
    for current, directories, filenames in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded_directories)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                file_path = Path(current) / filename
                files.append(str(file_path.relative_to(current_directory)))
                if len(files) == 100:
                    return files
    return files


def python_list_files(directory: str = ".") -> str:
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


def python_read_file(file_path: str) -> str:
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 파일 읽기 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        with tokenize.open(source_path) as source_file:
            source = source_file.read()
        if len(source.encode("utf-8")) > LIVE_ANALYSIS_MAX_BODY:
            return "Python 파일 읽기 실패: 파일이 1 MB 제한을 초과했습니다."
        return f"파일: {source_path.name}\n```python\n{source}\n```"
    except (OSError, UnicodeError, ValueError) as error:
        return f"Python 파일 읽기 실패: {error}"


def read_workspace_text(path: Path) -> tuple[str, str]:
    contents = path.read_bytes()
    if path.suffix.lower() == ".py":
        encoding, _ = tokenize.detect_encoding(BytesIO(contents).readline)
    else:
        encoding = "utf-8-sig" if contents.startswith(b"\xef\xbb\xbf") else "utf-8"
    return contents.decode(encoding), encoding


def read_workspace_file(file_path: str) -> str:
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file():
            return f"파일 읽기 실패: 작업 폴더 안의 기존 파일을 지정해 주세요: {file_path}"
        source, _ = read_workspace_text(source_path)
        if len(source.encode("utf-8")) > LIVE_ANALYSIS_MAX_BODY:
            return "파일 읽기 실패: 파일이 1 MB 제한을 초과했습니다."
        language = source_path.suffix.lstrip(".")
        return f"파일: {source_path}\n```{language}\n{source}\n```"
    except (OSError, UnicodeError, ValueError) as error:
        return f"파일 읽기 실패: {error}"


def write_workspace_file(
    file_path: str,
    source: str,
    expected_source: str,
    create: bool = False,
) -> str:
    temporary_path: Path | None = None
    try:
        target_path = resolve_workspace_path(file_path)
        if len(source.encode("utf-8")) > LIVE_ANALYSIS_MAX_BODY:
            return "파일 수정 실패: 수정안이 1 MB 제한을 초과했습니다."
        if not target_path.parent.is_dir():
            return f"파일 수정 실패: 상위 디렉터리가 없습니다: {target_path.parent}"

        exists = target_path.is_file()
        if target_path.exists() and not exists:
            return f"파일 수정 실패: 일반 파일만 수정할 수 있습니다: {target_path}"
        if create and exists:
            return f"파일 생성 충돌: {target_path} 파일이 이미 존재합니다."
        if exists:
            current_source, encoding = read_workspace_text(target_path)
            if current_source != expected_source:
                return f"파일 수정 충돌: {target_path} 파일이 읽은 뒤 변경되었습니다. 최신 내용을 다시 읽어 주세요."
        elif not create or expected_source:
            return f"파일 수정 실패: 기존 파일이 없습니다. 새 파일 생성 요청인지 확인해 주세요: {target_path}"
        else:
            encoding = (
                tokenize.detect_encoding(BytesIO(source.encode("utf-8")).readline)[0]
                if target_path.suffix.lower() == ".py"
                else "utf-8"
            )

        if target_path.suffix.lower() == ".py":
            try:
                ast.parse(source, filename=str(target_path))
            except SyntaxError as error:
                return f"Python 파일 수정 실패: 수정안에 문법 오류가 있습니다 ({error.lineno}:{error.offset} {error.msg})."

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(source)

        if exists:
            latest_source, _ = read_workspace_text(target_path)
            if latest_source != expected_source:
                return f"파일 수정 충돌: {target_path} 저장 직전에 파일이 변경되었습니다. 최신 내용을 다시 읽어 주세요."
        elif target_path.exists():
            return f"파일 수정 충돌: {target_path} 파일이 생성 요청 이후 이미 만들어졌습니다."

        os.replace(temporary_path, target_path)
        temporary_path = None
        saved_source, _ = read_workspace_text(target_path)
        if saved_source != source:
            return f"파일 저장 확인 실패: {target_path}"
        return f"파일 수정 및 저장 완료: {target_path}"
    except (OSError, UnicodeError, ValueError) as error:
        return f"파일 수정 실패: {error}"
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def python_check_syntax(file_path: str) -> str:
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 문법 검사 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        with tokenize.open(source_path) as source_file:
            source = source_file.read()
        ast.parse(source, filename=str(source_path))
        return f"Python 문법 검사 통과: {source_path.name}"
    except SyntaxError as error:
        return f"Python 문법 오류: {file_path}:{error.lineno}:{error.offset} {error.msg}"
    except (OSError, UnicodeError, ValueError) as error:
        return f"Python 문법 검사 실패: {error}"


def python_run_file(file_path: str) -> str:
    if not allow_python_run:
        return "Python 실행이 비활성화되어 있습니다. /python-run on 명령으로 허용해 주세요."
    try:
        source_path = resolve_workspace_path(file_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".py":
            return "Python 실행 실패: 작업 폴더 안의 .py 파일을 지정해 주세요."
        result = subprocess.run(
            [sys.executable, str(source_path)],
            cwd=current_directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        output = output[:6000] + ("\n... (출력 일부 생략)" if len(output) > 6000 else "")
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


def _rag_index_path() -> Path:
    return current_directory / ".quasar" / RAG_INDEX_FILENAME


def _rag_tokens(text: str) -> list[str]:
    tokens = []
    for word in re.findall(r"[가-힣]+|[a-z0-9_]+", text.lower()):
        tokens.append(word)
        if re.fullmatch(r"[가-힣]{2,}", word):
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
    return tokens


def _rag_chunks(text: str, relative_path: str) -> list[dict[str, str]]:
    normalized_text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks = []
    start = 0
    while start < len(normalized_text):
        end = min(start + RAG_CHUNK_SIZE, len(normalized_text))
        if end < len(normalized_text):
            boundary = normalized_text.rfind("\n", start + RAG_CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        content = normalized_text[start:end].strip()
        if content:
            chunks.append({"path": relative_path, "text": content})
        if end >= len(normalized_text):
            break
        start = max(start + 1, end - RAG_CHUNK_OVERLAP)
    return chunks


def rag_index_documents(directory: str = ".") -> str:
    try:
        folder = resolve_workspace_path(directory)
        if not folder.is_dir():
            return f"RAG 색인 실패: 디렉터리가 아닙니다: {directory}"

        chunks = []
        indexed_files = 0
        examined_files = 0
        skipped_files = []
        reached_file_limit = False
        reached_chunk_limit = False
        for current, directories, filenames in os.walk(folder, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in RAG_EXCLUDED_DIRECTORIES)
            for filename in sorted(filenames):
                source_path = Path(current) / filename
                if source_path.suffix.lower() not in RAG_SUPPORTED_SUFFIXES:
                    continue
                if examined_files >= RAG_MAX_FILES:
                    reached_file_limit = True
                    break
                examined_files += 1
                try:
                    if source_path.is_symlink():
                        skipped_files.append(f"{source_path.name} (심볼릭 링크)")
                        continue
                    if source_path.stat().st_size > RAG_MAX_FILE_SIZE:
                        skipped_files.append(f"{source_path.name} (파일 크기 제한)")
                        continue
                    if source_path.suffix.lower() == ".py":
                        with tokenize.open(source_path) as source_file:
                            contents = source_file.read()
                    else:
                        contents = source_path.read_text(encoding="utf-8")
                    relative_path = str(source_path.relative_to(current_directory))
                    file_chunks = _rag_chunks(contents, relative_path)
                    remaining_chunks = RAG_MAX_CHUNKS - len(chunks)
                    chunks.extend(file_chunks[:remaining_chunks])
                    indexed_files += 1
                    if len(file_chunks) > remaining_chunks:
                        reached_chunk_limit = True
                        break
                except (OSError, UnicodeError) as error:
                    skipped_files.append(f"{source_path.name} ({error})")
            if reached_file_limit or reached_chunk_limit:
                break

        index_path = _rag_index_path()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", dir=index_path.parent, delete=False
            ) as index_file:
                temporary_path = Path(index_file.name)
                json.dump({"chunks": chunks}, index_file, ensure_ascii=False)
            temporary_path.replace(index_path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

        summary = f"RAG 색인 완료: {indexed_files}개 파일, {len(chunks)}개 청크 ({index_path})"
        if reached_file_limit:
            summary += f"\n파일은 최대 {RAG_MAX_FILES}개까지만 색인했습니다."
        if reached_chunk_limit:
            summary += f"\n청크는 최대 {RAG_MAX_CHUNKS}개까지만 색인했습니다."
        if skipped_files:
            summary += "\n건너뛴 파일: " + ", ".join(skipped_files[:10])
            if len(skipped_files) > 10:
                summary += f" 외 {len(skipped_files) - 10}개"
        return summary
    except (OSError, ValueError) as error:
        return f"RAG 색인 실패: {error}"


def rag_search(query: str, top_k: int = 5) -> str:
    if not query.strip():
        return "RAG 검색 실패: 검색어를 입력해 주세요."
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 10:
        return "RAG 검색 실패: 결과 개수는 1~10 사이여야 합니다."

    index_path = _rag_index_path()
    if not index_path.is_file():
        return "RAG 색인을 찾지 못했습니다. 먼저 rag_index_documents 도구로 문서를 색인해 주세요."
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        chunks = index.get("chunks")
        if not isinstance(chunks, list):
            raise ValueError("색인 파일 형식이 올바르지 않습니다.")
        valid_chunks = [
            chunk for chunk in chunks
            if isinstance(chunk, dict) and isinstance(chunk.get("path"), str) and isinstance(chunk.get("text"), str)
        ]
        query_tokens = _rag_tokens(query)
        query_terms = set(query_tokens)
        if not query_terms:
            return "RAG 검색 실패: 검색어에서 검색할 단어를 찾지 못했습니다."

        tokenized_chunks = [_rag_tokens(chunk["text"]) for chunk in valid_chunks]
        document_frequency: dict[str, int] = {}
        for tokens in tokenized_chunks:
            for term in set(tokens):
                document_frequency[term] = document_frequency.get(term, 0) + 1

        document_count = len(valid_chunks)
        if not document_count:
            return "RAG 색인이 비어 있습니다. 문서 폴더를 다시 색인해 주세요."
        average_length = sum(map(len, tokenized_chunks)) / document_count or 1
        scored_chunks = []
        for chunk, tokens in zip(valid_chunks, tokenized_chunks):
            frequencies: dict[str, int] = {}
            for token in tokens:
                frequencies[token] = frequencies.get(token, 0) + 1
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                inverse_frequency = math.log(
                    1 + (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
                )
                score += inverse_frequency * frequency * 2.5 / (
                    frequency + 1.5 * (0.25 + 0.75 * len(tokens) / average_length)
                )
            if score:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda item: item[0], reverse=True)
        if not scored_chunks:
            return "관련 문서를 찾지 못했습니다. 다른 검색어를 사용하거나 문서를 다시 색인해 주세요."
        results = [
            f"[출처: {chunk['path']}]\n{chunk['text']}"
            for _, chunk in scored_chunks[:top_k]
        ]
        return "RAG 검색 결과:\n\n" + "\n\n---\n\n".join(results)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return f"RAG 검색 실패: {error}"


def open_in_vscode(path: str = ".") -> str:
    executable = shutil.which("code")
    if not executable:
        return "VS Code 열기 실패: 'code' 명령을 찾을 수 없습니다."
    try:
        target = resolve_workspace_path(path)
        if not target.exists():
            return f"VS Code 열기 실패: 경로를 찾을 수 없습니다: {path}"
        subprocess.Popen([executable, str(target)], cwd=current_directory)
        return f"VS Code에서 열었습니다: {target}"
    except (OSError, ValueError) as error:
        return f"VS Code 열기 실패: {error}"


class CliLiveAnalysisServer:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._editor_update_condition = threading.Condition(self._lock)
        self._latest_document: dict[str, Any] | None = None
        self._last_diagnostics: dict[str, tuple[int | None, int | None, str] | None] = {}
        self._pending_editor_update: dict[str, Any] | None = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._create_handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="quasar-cli-live-analysis",
            daemon=True,
        )
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/live-analysis"

    def _create_handler(self):
        analysis_server = self

        class LiveAnalysisHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/editor-update":
                    self.send_error(404)
                    return
                if self.headers.get("Origin"):
                    self.send_error(403)
                    return
                authorization = self.headers.get("Authorization", "")
                supplied_token = authorization.removeprefix("Bearer ").strip()
                if not hmac.compare_digest(supplied_token, analysis_server.token):
                    self.send_error(401)
                    return
                update = analysis_server.pending_editor_update()
                if not update:
                    self.send_response(204)
                    self.end_headers()
                    return
                body = json.dumps(update).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path not in {"/live-analysis", "/editor-update/ack"}:
                    self.send_error(404)
                    return
                if self.headers.get("Origin"):
                    self.send_error(403)
                    return

                content_length = self.headers.get("Content-Length", "")
                if not content_length.isdigit():
                    self.send_error(400)
                    return
                if int(content_length) > LIVE_ANALYSIS_MAX_BODY:
                    self.send_error(413)
                    return

                authorization = self.headers.get("Authorization", "")
                supplied_token = authorization.removeprefix("Bearer ").strip()
                if not hmac.compare_digest(supplied_token, analysis_server.token):
                    self.send_error(401)
                    return

                try:
                    payload = json.loads(self.rfile.read(int(content_length)))
                    if self.path == "/editor-update/ack":
                        update_id = payload["id"]
                        status = payload["status"]
                        if not isinstance(update_id, str) or status not in {
                            "applied",
                            "saved",
                            "save_failed",
                            "conflict",
                        }:
                            raise ValueError("invalid editor update acknowledgment")
                        analysis_server.acknowledge_editor_update(update_id, status)
                        self.send_response(204)
                        self.end_headers()
                        return
                    file_path = payload["path"]
                    source = payload["source"]
                    if not isinstance(file_path, str) or not isinstance(source, str):
                        raise ValueError("path and source must be strings")
                    target = Path(file_path).resolve()
                    target.relative_to(analysis_server.workspace)
                    if target.suffix.lower() != ".py":
                        raise ValueError("only Python files are supported")
                except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
                    self.send_error(400)
                    return

                analysis_server._receive_document(target, source)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                return

        return LiveAnalysisHandler

    def _receive_document(self, path: Path, source: str) -> None:
        try:
            ast.parse(source, filename=str(path))
            diagnostic = None
        except SyntaxError as error:
            diagnostic = (error.lineno, error.offset, error.msg)

        with self._lock:
            path_key = str(path)
            previous_diagnostic = self._last_diagnostics.get(path_key)
            now = time.time()
            previous_document = self._latest_document
            changed_at = now
            if (
                previous_document
                and previous_document["path"] == path
                and previous_document["source"] == source
            ):
                changed_at = previous_document["changed_at"]
            self._latest_document = {
                "path": path,
                "source": source,
                "updated_at": now,
                "changed_at": changed_at,
            }
            self._last_diagnostics[path_key] = diagnostic

        if diagnostic and diagnostic != previous_diagnostic:
            line, column, message = diagnostic
            print(f"\n[VS Code 문법 오류] {path}:{line}:{column} {message}", flush=True)
        elif diagnostic is None and previous_diagnostic:
            print(f"\n[VS Code 문법 검사 통과] {path}", flush=True)

    def latest_document(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest_document.copy() if self._latest_document else None

    def queue_editor_update(self, source: str, save: bool = False) -> str:
        if len(source.encode("utf-8")) > LIVE_ANALYSIS_MAX_BODY:
            return "VS Code 편집기 수정 실패: 수정안이 1 MB 제한을 초과했습니다."
        with self._editor_update_condition:
            document = self._latest_document
            if not document:
                return "현재 VS Code 편집기 내용이 없습니다. 파일을 열고 연결 상태를 확인해 주세요."
            if self._pending_editor_update:
                return "이전 편집기 수정이 아직 VS Code에서 처리되지 않았습니다."
            self._pending_editor_update = {
                "id": secrets.token_urlsafe(16),
                "path": str(document["path"]),
                "base_source": document["source"],
                "source": source,
                "save": save,
                "status": None,
            }
            update = self._pending_editor_update
            acknowledged = self._editor_update_condition.wait_for(
                lambda: update["status"] is not None,
                timeout=15,
            )
            if self._pending_editor_update is update:
                self._pending_editor_update = None
            if not acknowledged:
                return (
                    "VS Code 수정 요청을 보냈지만 15초 안에 적용 확인을 받지 못했습니다. "
                    "편집기 연결 상태를 확인한 뒤 다시 요청해 주세요."
                )
            if update["status"] == "saved":
                return f"VS Code에서 수정하고 저장했습니다: {document['path']}"
            if update["status"] == "save_failed":
                return (
                    f"VS Code 편집기에는 수정했지만 저장에 실패했습니다: {document['path']}. "
                    "편집기에서 저장 상태를 확인해 주세요."
                )
            if update["status"] == "applied":
                return f"VS Code 편집기에 수정 내용을 적용했습니다 (저장하지 않음): {document['path']}"
            return "VS Code 편집 내용이 요청 이후 변경되어 수정안을 적용하지 않았습니다. 최신 내용을 확인한 뒤 다시 요청해 주세요."

    def pending_editor_update(self) -> dict[str, Any] | None:
        with self._lock:
            update = self._pending_editor_update
            if not update or update["status"] is not None:
                return None
            return update.copy()

    def acknowledge_editor_update(self, update_id: str, status: str) -> None:
        with self._editor_update_condition:
            update = self._pending_editor_update
            if update and hmac.compare_digest(update["id"], update_id) and update["status"] is None:
                update["status"] = status
                if status in {"applied", "saved", "save_failed"} and self._latest_document:
                    self._latest_document["source"] = update["source"]
                    self._latest_document["updated_at"] = time.time()
                self._editor_update_condition.notify_all()
        if status == "conflict":
            print(
                "\n[VS Code 편집 충돌] 적용 대기 중 사용자가 파일을 수정해 변경을 적용하지 않았습니다.",
                flush=True,
            )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class CliCollaborationMonitor:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        live_analysis_server: CliLiveAnalysisServer,
        settings: dict[str, int],
    ):
        self.client = client
        self.model = model
        self.live_analysis_server = live_analysis_server
        self.poll_interval = settings["poll_interval_seconds"]
        self.stable_seconds = settings["stable_seconds"]
        self.idle_seconds = settings["idle_seconds"]
        self._lock = threading.Lock()
        self._enabled = False
        self._stop_event = threading.Event()
        self._reviewed_versions: dict[str, str] = {}
        self._idle_asked_versions: dict[str, str] = {}
        self._questioned_versions: set[tuple[str, str]] = set()
        self._pending_questions: list[str] = []
        self._thread = threading.Thread(
            target=self._run,
            name="quasar-cli-collaboration-monitor",
            daemon=True,
        )
        self._thread.start()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def take_pending_questions(self) -> list[str]:
        with self._lock:
            questions = self._pending_questions[:]
            self._pending_questions.clear()
            return questions

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            if not self.is_enabled():
                continue

            document = self.live_analysis_server.latest_document()
            if not document:
                continue

            path = str(document["path"])
            source = document["source"]
            version = hashlib.sha256(source.encode("utf-8")).hexdigest()
            quiet_seconds = time.time() - document["changed_at"]
            if quiet_seconds < self.stable_seconds:
                continue

            if self._reviewed_versions.get(path) != version:
                self._reviewed_versions[path] = version
                self._ask_if_needed(path, source, quiet_seconds, idle_check=False)

            if (
                quiet_seconds >= self.idle_seconds
                and (path, version) not in self._questioned_versions
            ):
                if self._idle_asked_versions.get(path) != version:
                    self._idle_asked_versions[path] = version
                    self._ask_if_needed(path, source, quiet_seconds, idle_check=True)

    def _ask_if_needed(
        self,
        path: str,
        source: str,
        quiet_seconds: float,
        idle_check: bool,
    ) -> None:
        version = hashlib.sha256(source.encode("utf-8")).hexdigest()
        code_excerpt = source
        truncated = len(code_excerpt) > 24_000
        if truncated:
            code_excerpt = code_excerpt[:12_000] + "\n# ... 중간 내용 생략 ...\n" + code_excerpt[-12_000:]

        if idle_check:
            condition = (
                f"이 파일은 약 {int(quiet_seconds)}초 동안 변경되지 않았습니다. "
                "작업이 멈춘 듯하거나 목표가 불분명해 확인이 필요할 때만 짧게 질문하세요."
            )
        else:
            condition = (
                f"이 파일은 약 {int(quiet_seconds)}초 동안 변경되지 않았습니다. "
                "잠재적인 오류 또는 작업을 막는 구체적인 문제를 찾으세요."
            )
        prompt = (
            f"파일: {path}\n{condition}\n"
            "실제로 도움이 필요한 구체적이고 가능성 높은 문제일 때만 한국어 질문 하나를 출력하세요. "
            "문제나 확인할 사항이 없으면 정확히 NO_QUESTION만 출력하세요. "
            "코드를 수정하거나 일반적인 칭찬/상태 확인 질문을 하지 마세요.\n\n"
            f"```python\n{code_excerpt}\n```"
        )
        try:
            review_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a collaborative coding partner who does not interrupt the user's flow. "
                        "Intervene only for concrete, likely issues such as potential bugs or blockers. "
                        "Defer review while the user is actively editing code. "
                        "Output exactly one question or NO_QUESTION, and nothing else."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            if is_responses_model(self.model):
                answer = request_responses_text(self.client, self.model, review_messages).strip()
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=review_messages,
                )
                answer = (response.choices[0].message.content or "").strip()
            latest_document = self.live_analysis_server.latest_document()
            if (
                not self.is_enabled()
                or not latest_document
                or str(latest_document["path"]) != path
                or latest_document["source"] != source
                or time.time() - latest_document["changed_at"] < self.stable_seconds
            ):
                return
            if answer and answer.upper() != "NO_QUESTION":
                with self._lock:
                    self._pending_questions.append(answer)
                    self._questioned_versions.add((path, version))
                print(f"\n[공동작업 질문] {answer}", flush=True)
        except Exception as error:
            print(f"\n[공동작업 검토 실패] {path}: {error}", file=sys.stderr, flush=True)

    def close(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)


def get_current_time() -> str:
    return f"현재 시간은 {datetime.now().strftime('%Y년 %m월 %d일 %H시 %M분 %S초')} 입니다."


def _trigger_alarm(message: str) -> None:
    for _ in range(3):
        if platform.system() == "Windows":
            winsound.Beep(1000, 500)
        else:
            print("\a", end="", flush=True)
        time.sleep(0.2)
    print(f"\n🔔 [알람/타이머 종료!] {message}")


def set_timer(seconds: int, message: str) -> str:
    if seconds <= 0:
        return "타이머 설정 실패: 초는 1 이상이어야 합니다."
    timer = threading.Timer(seconds, _trigger_alarm, args=[message])
    timer.daemon = True
    timer.start()
    return f"{seconds}초 뒤에 '{message}' 알림이 울리도록 타이머를 설정했습니다."


def set_alarm(target_time: str, message: str) -> str:
    try:
        hour, minute = map(int, target_time.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        timer = threading.Timer((target - now).total_seconds(), _trigger_alarm, args=[message])
        timer.daemon = True
        timer.start()
        return f"{target_time}에 '{message}' 알림이 울리도록 설정했습니다."
    except ValueError:
        return "알람 설정 실패: 시간 형식은 HH:MM이어야 합니다."


def _run_scheduled_command(command: str, working_directory: Path, job: dict[str, str]) -> None:
    encoding = "cp949" if platform.system() == "Windows" else "utf-8"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            check=True,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
        )
        job["status"] = f"완료: {result.stdout.strip() or '출력 없음'}"
    except subprocess.CalledProcessError as error:
        job["status"] = f"실패 (코드 {error.returncode}): {error.stderr.strip()}"
    except OSError as error:
        job["status"] = f"오류: {error}"
    _trigger_alarm(f"예약 명령어 완료: {command}")


def schedule_command(target_datetime: str, command: str) -> str:
    if not auto_approve_commands:
        return "예약 명령어는 백그라운드 실행이므로 /approve on으로 명령 자동 승인을 먼저 켜주세요."
    try:
        target = datetime.strptime(target_datetime, "%Y-%m-%d %H:%M")
        delay = (target - datetime.now()).total_seconds()
        if delay <= 0:
            return "예약 시각은 현재 시각보다 미래여야 합니다."
        job = {"target": target.strftime("%Y-%m-%d %H:%M"), "command": command, "status": "대기 중"}
        scheduled_jobs.append(job)
        timer = threading.Timer(delay, _run_scheduled_command, args=[command, current_directory, job])
        timer.daemon = True
        timer.start()
        return f"{target_datetime}에 '{command}' 명령어를 예약했습니다."
    except ValueError:
        return "예약 실패: 날짜 형식은 YYYY-MM-DD HH:MM이어야 합니다."


def create_calendar_event(title: str, date: str, event_time: str, description: str = "") -> str:
    try:
        parsed_date = datetime.strptime(date, "%Y%m%d")
        parsed_time = datetime.strptime(event_time, "%H%M%S")
        safe_title = title.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        safe_description = description.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        ics_content = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Quasar CLI//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"SUMMARY:{safe_title}\r\nDESCRIPTION:{safe_description}\r\n"
            f"DTSTART:{parsed_date:%Y%m%d}T{parsed_time:%H%M%S}\r\n"
            f"DTEND:{parsed_date:%Y%m%d}T{parsed_time:%H%M%S}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".ics", prefix="quasar_event_", delete=False
        ) as event_file:
            event_file.write(ics_content)
            file_path = Path(event_file.name)
        if platform.system() == "Windows":
            os.startfile(file_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(file_path)])
        else:
            subprocess.Popen(["xdg-open", str(file_path)])
        return f"일정 파일이 생성되어 캘린더 앱을 열었습니다. (파일 경로: {file_path})"
    except (OSError, ValueError) as error:
        return f"캘린더 연결 중 오류 발생: {error}"


def capture_screen() -> tuple[str, int, int]:
    screenshot = ImageGrab.grab()
    image_buffer = BytesIO()
    screenshot.save(image_buffer, format="PNG")
    image_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    return image_data, screenshot.width, screenshot.height


def get_browser():
    global browser, chrome_process
    if browser is None:
        profile_directory = current_directory / ".quasar" / "chrome-profile"
        profile_directory.mkdir(parents=True, exist_ok=True)
        chrome_paths = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        chrome_binary = next((path for path in chrome_paths if path.is_file()), None)
        if chrome_binary is None:
            raise FileNotFoundError("Google Chrome was not found in the standard Windows installation paths.")
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = 4  # SW_SHOWNOACTIVATE
        chrome_process = subprocess.Popen(
            [
                str(chrome_binary),
                f"--user-data-dir={profile_directory}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=9222",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startup_info,
        )
        deadline = time.monotonic() + BROWSER_STARTUP_TIMEOUT_SECONDS
        while True:
            if chrome_process.poll() is not None:
                raise RuntimeError(f"Chrome exited before its DevTools endpoint started (code {chrome_process.returncode}).")
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as response:
                    response.read()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    chrome_process.terminate()
                    chrome_process.wait(timeout=5)
                    chrome_process = None
                    raise TimeoutError(
                        f"Chrome DevTools endpoint did not start within {BROWSER_STARTUP_TIMEOUT_SECONDS} seconds."
                    )
                time.sleep(BROWSER_POLL_INTERVAL_SECONDS)
        options = webdriver.ChromeOptions()
        options.debugger_address = "127.0.0.1:9222"
        options.page_load_strategy = "eager"
        try:
            browser = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=options,
            )
        except WebDriverException:
            if chrome_process.poll() is None:
                chrome_process.terminate()
                chrome_process.wait(timeout=5)
            chrome_process = None
            raise
        browser.implicitly_wait(0)
    return browser


def close_browser() -> None:
    global browser, chrome_process
    if browser is not None:
        browser.quit()
        browser = None
    if chrome_process is not None and chrome_process.poll() is None:
        chrome_process.terminate()
        chrome_process.wait(timeout=5)
    chrome_process = None


class DuckDuckGoResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current_result: dict[str, str] | None = None
        self._result_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._capture_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if self._current_result is None and tag == "div" and "result" in classes:
            self._current_result = {"title": "", "url": "", "snippet": ""}
            self._result_depth = 1
            return

        if self._current_result is None:
            return
        if tag == "div":
            self._result_depth += 1
        if self._capture is not None:
            self._capture_depth += 1
            return
        if tag == "a" and "result__a" in classes:
            self._current_result["url"] = self._normalize_result_url(attributes.get("href") or "")
            self._capture = "title"
            self._capture_depth = 1
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._capture_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture is not None:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                if self._current_result is not None:
                    self._current_result[self._capture] = " ".join(
                        "".join(self._capture_text).split()
                    )
                self._capture = None
                self._capture_text = []

        if self._current_result is not None and tag == "div":
            self._result_depth -= 1
            if self._result_depth == 0:
                if self._current_result["title"] and self._current_result["url"]:
                    self.results.append(self._current_result)
                self._current_result = None

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture_text.append(data)

    @staticmethod
    def _normalize_result_url(href: str) -> str:
        absolute_url = urljoin("https://duckduckgo.com", href)
        redirected_url = parse_qs(urlsplit(absolute_url).query).get("uddg", [""])[0]
        return redirected_url or absolute_url


def web_search(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        return "웹 검색 실패: 검색어를 입력해 주세요."
    query = query.strip()
    if len(query) > 500:
        return "웹 검색 실패: 검색어는 500자 이내로 입력해 주세요."

    request = urllib.request.Request(
        f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
        headers={
            "Accept": "text/html",
            "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            page = response.read().decode("utf-8", errors="replace")
        parser = DuckDuckGoResultsParser()
        parser.feed(page)
        if not parser.results:
            return "웹 검색 결과를 찾지 못했습니다. 검색어를 바꿔 다시 시도해 주세요."
        return "\n---\n".join(
            f"제목: {result['title']}\n링크: {result['url']}\n내용 요약: {result['snippet']}"
            for result in parser.results[:5]
        )
    except (OSError, TimeoutError, ValueError) as error:
        return f"웹 검색 실패: {error}"


def web_navigate(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        return "페이지 이동 실패: URL을 입력해 주세요."
    url = url.strip()
    if any(character.isspace() for character in url):
        return "페이지 이동 실패: URL에 공백을 포함할 수 없습니다."
    try:
        parsed_url = urlsplit(url)
    except ValueError as error:
        return f"페이지 이동 실패: 잘못된 URL입니다 ({error})."
    if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.hostname:
        return "페이지 이동 실패: http:// 또는 https://로 시작하는 전체 URL을 입력해 주세요."
    if parsed_url.username is not None or parsed_url.password is not None:
        return "페이지 이동 실패: 계정 정보가 포함된 URL은 열 수 없습니다."
    try:
        page = get_browser()
        page.get(url)
        return f"페이지 이동 완료\n제목: {page.title}\n현재 URL: {page.current_url}"
    except Exception as error:
        return f"페이지 이동 실패 ({url}): {type(error).__name__}: {error}"


def web_click(css_selector: str) -> str:
    try:
        page = get_browser()
        element = WebDriverWait(page, BROWSER_ELEMENT_WAIT_SECONDS).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector))
        )
        element.click()
        return f"요소 클릭 완료: {css_selector}\n현재 URL: {page.current_url}"
    except Exception as error:
        return f"요소 클릭 실패: {error}"


def web_input(css_selector: str, text: str, press_enter: bool = False) -> str:
    try:
        page = get_browser()
        element = WebDriverWait(page, BROWSER_ELEMENT_WAIT_SECONDS).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        element.click()
        is_contenteditable = (
            element.get_attribute("contenteditable") == "true"
            or element.get_attribute("role") == "textbox"
        )
        if is_contenteditable:
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
        else:
            element.clear()
        element.send_keys(text)
        actual_value = (
            element.get_attribute("value")
            if not is_contenteditable
            else element.get_property("innerText")
        )
        if actual_value is None:
            actual_value = element.text
        actual_content = " ".join(str(actual_value).split())
        expected_content = " ".join(text.split())
        if actual_content != expected_content:
            return (
                f"입력 확인 실패: {css_selector}에 입력을 시도했지만 내용이 일치하지 않습니다. "
                "전송하지 않았습니다."
            )
        if press_enter:
            element.send_keys(Keys.ENTER)
        action_status = "입력 및 Enter 전송 완료" if press_enter else "입력 확인 완료"
        return f"{action_status}: {css_selector}\n현재 URL: {page.current_url}"
    except Exception as error:
        return f"입력 실패 ({css_selector}): {type(error).__name__}: {error}"


def web_scroll(direction: str) -> str:
    if direction not in {"up", "down"}:
        return "스크롤 실패: 방향은 up 또는 down이어야 합니다."
    try:
        page = get_browser()
        offset = "window.innerHeight * 0.8" if direction == "down" else "-window.innerHeight * 0.8"
        page.execute_script(f"window.scrollBy(0, {offset});")
        return f"스크롤 완료 ({direction})\n{page.find_element(By.TAG_NAME, 'body').text[:1500]}"
    except Exception as error:
        return f"스크롤 실패: {error}"


def web_back() -> str:
    try:
        page = get_browser()
        page.back()
        return f"이전 페이지로 이동 완료: {page.current_url}"
    except Exception as error:
        return f"뒤로 가기 실패: {error}"


def web_key_press(key: str, text: str = "") -> str:
    try:
        page = get_browser()
        actions = ActionChains(page)
        if text:
            actions.send_keys(text)
        if key:
            key_value = getattr(Keys, key.upper(), key)
            actions.send_keys(key_value)
        actions.perform()
        return f"키보드 입력 성공: {text}{key}"
    except Exception as error:
        return f"키보드 입력 실패: {error}"


def gmail_open() -> str:
    try:
        page = get_browser()
        page.get("https://mail.google.com/mail/u/0/#inbox")
        time.sleep(3)
        if "accounts.google.com" in page.current_url:
            return (
                "Quasar 전용 Chrome에서 Google 로그인이 필요합니다. 열린 창에서 로그인해 주세요. "
                "로그인 상태는 다음 실행을 위해 이 작업 폴더의 .quasar\\chrome-profile에 유지됩니다."
            )
        return f"Gmail을 열었습니다. 현재 URL: {page.current_url}"
    except Exception as error:
        return f"Gmail 열기 실패: {error}"


def gmail_search(query: str) -> str:
    try:
        page = get_browser()
        if "mail.google.com" not in page.current_url:
            gmail_open()
        search_box = WebDriverWait(page, 10).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "input[placeholder='Search mail'], input[aria-label='Search mail']")
            )
        )
        search_box.clear()
        search_box.send_keys(query, Keys.ENTER)
        time.sleep(2)
        return f"Gmail 검색 완료: {query}\n{page.find_element(By.TAG_NAME, 'body').text[:4000]}"
    except Exception as error:
        return f"Gmail 검색 실패: {error}"


def gmail_create_draft(to: str, subject: str, body: str) -> str:
    try:
        page = get_browser()
        if "mail.google.com" not in page.current_url:
            gmail_open()
        compose = WebDriverWait(page, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[self::div or self::button][@role='button' and (contains(@aria-label, 'Compose') or contains(@aria-label, '편지쓰기'))]")
            )
        )
        compose.click()
        to_box = WebDriverWait(page, 10).until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[aria-label='To recipients'], input[peoplekit-id='BbVjBd']")
            )
        )
        to_box.send_keys(to, Keys.ENTER)
        page.find_element(By.NAME, "subjectbox").send_keys(subject)
        body_box = page.find_element(By.CSS_SELECTOR, "div[aria-label='Message Body'], div[aria-label='메시지 본문']")
        body_box.send_keys(body)
        body_box.send_keys(Keys.ESCAPE)
        time.sleep(1)
        return f"Gmail 임시저장 완료: 받는 사람={to}, 제목={subject} (메일은 전송하지 않았습니다.)"
    except Exception as error:
        return f"Gmail 임시저장 실패: {error}"


def gmail_send(to: str, subject: str, body: str) -> str:
    if not to.strip() or not subject.strip() or not body.strip():
        return "Gmail 전송 실패: 받는 사람, 제목, 본문은 비워둘 수 없습니다."

    try:
        page = get_browser()
        if "mail.google.com" not in page.current_url:
            page.get("https://mail.google.com/mail/u/0/#inbox")
        if "accounts.google.com" in page.current_url:
            return (
                "Quasar 전용 Chrome에 Gmail 로그인이 필요합니다. "
                "`/gmail` 명령으로 열린 창에서 직접 로그인한 뒤 전송을 다시 요청해 주세요."
            )
        WebDriverWait(page, 10).until(lambda driver: "mail.google.com" in driver.current_url)

        compose = WebDriverWait(page, 10).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//*[self::div or self::button][@role='button' and "
                    "(contains(@aria-label, 'Compose') or contains(@aria-label, '편지쓰기'))]",
                )
            )
        )
        compose.click()
        to_box = WebDriverWait(page, 10).until(
            EC.visibility_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "input[aria-label='To recipients'], input[peoplekit-id='BbVjBd'], "
                    "input[name='to'], textarea[name='to'], input[aria-label*='받는'], "
                    "textarea[aria-label*='받는']",
                )
            )
        )
        to_box.send_keys(to, Keys.ENTER)
        subject_box = WebDriverWait(page, 10).until(
            EC.visibility_of_element_located((By.NAME, "subjectbox"))
        )
        subject_box.send_keys(subject)
        body_box = WebDriverWait(page, 10).until(
            EC.visibility_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "div[aria-label='Message Body'], div[aria-label='메시지 본문']",
                )
            )
        )
        body_box.send_keys(body)
        previous_body = page.find_element(By.TAG_NAME, "body").text
        send_button = WebDriverWait(page, 10).until(
            EC.element_to_be_clickable(
                (
                    By.CSS_SELECTOR,
                    "div[role='button'][aria-label*='Send'], "
                    "div[role='button'][aria-label*='보내기'], "
                    "div[role='button'][data-tooltip*='Send'], "
                    "div[role='button'][data-tooltip*='보내기']",
                )
            )
        )
        send_button.click()

        confirmations = ("Message sent", "메시지가 전송되었습니다", "메일이 전송되었습니다")

        def send_confirmed(driver):
            current_body = driver.find_element(By.TAG_NAME, "body").text
            return any(
                confirmation in current_body and confirmation not in previous_body
                for confirmation in confirmations
            )

        WebDriverWait(page, 15).until(send_confirmed)
        return f"Gmail 전송 완료: 받는 사람={to}, 제목={subject} (Gmail 전송 알림 확인)"
    except Exception as error:
        return (
            f"Gmail 전송 상태를 확인하지 못했습니다: {error}. "
            "메일이 전송됐을 가능성도 있으니 Gmail 보낸편지함을 확인한 뒤 재전송해 주세요."
        )


def is_screen_request(text: str) -> bool:
    browser_action_terms = ("브라우저", "크롬", "웹페이지", "사이트", "클릭", "눌러", "입력", "검색", "이동", "스크롤", "뒤로")
    if any(term in text for term in browser_action_terms):
        return False
    return any(term in text for term in ("화면", "스크린샷", "로고", "아이콘"))


class MCPStdioConnection:
    def __init__(self, name: str, config: dict[str, Any]) -> None:
        command = config.get("command")
        args = config.get("args", [])
        environment = config.get("env", {})
        if not isinstance(command, str) or not command:
            raise ValueError("command는 비어 있지 않은 문자열이어야 합니다.")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("args는 문자열 배열이어야 합니다.")
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise ValueError("env는 문자열 키와 값으로 구성된 객체여야 합니다.")

        self.name = name
        self.process = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, **environment},
        )
        self._responses: queue.Queue[dict[str, Any] | Exception | None] = queue.Queue()
        self._request_id = 0
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "Quasar CLI", "version": "1.0"},
                },
            )
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def _read_responses(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    self._responses.put(RuntimeError(f"잘못된 JSON 응답: {error}"))
                    return
                if not isinstance(message, dict):
                    self._responses.put(RuntimeError("JSON-RPC 응답은 객체여야 합니다."))
                    return
                self._responses.put(message)
        except OSError as error:
            self._responses.put(error)
        finally:
            self._responses.put(None)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.process.poll() is not None:
                raise RuntimeError(f"MCP 서버가 종료되었습니다 (코드 {self.process.returncode}).")
            self._request_id += 1
            request_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            if self.process.stdin is None:
                raise RuntimeError("MCP 서버 표준 입력을 사용할 수 없습니다.")
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

            deadline = time.monotonic() + 30
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"MCP 서버 응답 시간 초과: {method}")
                try:
                    message = self._responses.get(timeout=remaining)
                except queue.Empty as error:
                    raise TimeoutError(f"MCP 서버 응답 시간 초과: {method}") from error
                if message is None:
                    raise RuntimeError("MCP 서버가 응답하기 전에 표준 출력을 닫았습니다.")
                if isinstance(message, Exception):
                    raise message
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise RuntimeError(f"MCP {method} 오류: {message['error']}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"MCP {method} 결과가 객체가 아닙니다.")
                return result

    def _notify(self, method: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError(f"MCP 서버가 종료되었습니다 (코드 {self.process.returncode}).")
        if self.process.stdin is None:
            raise RuntimeError("MCP 서버 표준 입력을 사용할 수 없습니다.")
        self.process.stdin.write(json.dumps({
            "jsonrpc": "2.0",
            "method": method,
        }) + "\n")
        self.process.stdin.flush()

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            page = result.get("tools", [])
            if not isinstance(page, list):
                raise RuntimeError("MCP tools/list 결과에 tools 배열이 없습니다.")
            tools.extend(tool for tool in page if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        content = result.get("content", [])
        if not isinstance(content, list):
            content = []
        text_parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        structured_content = result.get("structuredContent")
        output = "\n".join(text_parts)
        if structured_content is not None:
            structured_text = json.dumps(structured_content, ensure_ascii=False)
            output = f"{output}\n{structured_text}".strip() if output else structured_text
        if not output:
            output = json.dumps(content, ensure_ascii=False) if content else "MCP 도구가 결과를 반환하지 않았습니다."
        return f"MCP 도구 오류: {output}" if result.get("isError") else output

    def close(self) -> None:
        if self.process.poll() is None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=2)


class MCPServerManager:
    def __init__(self, config_path: Path) -> None:
        self.connections: dict[str, MCPStdioConnection] = {}
        self.tools: dict[str, tuple[MCPStdioConnection, str, dict[str, Any]]] = {}
        self.pending_servers: dict[str, dict[str, Any]] = {}
        if not config_path.exists():
            return
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"MCP 설정 파일을 읽을 수 없습니다 ({config_path}): {error}") from error
        servers = config.get("mcpServers") if isinstance(config, dict) else None
        if not isinstance(servers, dict):
            raise ValueError("MCP 설정 파일에는 mcpServers 객체가 필요합니다.")

        for server_name, server_config in servers.items():
            if not isinstance(server_name, str) or not server_name or not isinstance(server_config, dict):
                print(f"[MCP 연결 실패] 잘못된 서버 설정: {server_name}", file=sys.stderr)
                continue
            try:
                if self._is_chrome_devtools_server(server_config):
                    self.pending_servers[server_name] = server_config
                else:
                    self._connect_server(server_name, server_config)
            except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                print(f"[MCP 연결 실패] {server_name}: {error}", file=sys.stderr)

    @staticmethod
    def _is_chrome_devtools_server(server_config: dict[str, Any]) -> bool:
        command = server_config.get("command", "")
        args = server_config.get("args", [])
        return isinstance(args, list) and any(
            isinstance(part, str) and "chrome-devtools-mcp" in part
            for part in [command, *args]
        )

    def _connect_server(self, server_name: str, server_config: dict[str, Any]) -> None:
        if server_name in self.connections:
            return
        if self._is_chrome_devtools_server(server_config):
            try:
                get_browser()
            except (OSError, RuntimeError, TimeoutError, WebDriverException) as error:
                raise RuntimeError(f"Chrome을 시작할 수 없습니다: {error}") from error
        connection = MCPStdioConnection(server_name, server_config)
        try:
            server_tools = connection.list_tools()
        except (OSError, RuntimeError, TimeoutError, ValueError):
            connection.close()
            raise

        self.connections[server_name] = connection
        used_names = {tool["function"]["name"] for tool in TOOLS}
        used_names.update(self.tools)
        for tool in server_tools:
            tool_name = tool.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            exposed_name = self._exposed_name(server_name, tool_name, used_names)
            used_names.add(exposed_name)
            self.tools[exposed_name] = (connection, tool_name, tool)

    @staticmethod
    def _exposed_name(server_name: str, tool_name: str, used_names: set[str]) -> str:
        base = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server_name}_{tool_name}")
        if len(base) > 64:
            base = f"{base[:55]}_{hashlib.sha256(base.encode()).hexdigest()[:8]}"
        if base in used_names:
            digest = hashlib.sha256(f"{server_name}:{tool_name}".encode()).hexdigest()[:8]
            base = f"{base[:55]}_{digest}"
            suffix = 2
            while base in used_names:
                base = f"{base[:52]}_{digest}_{suffix}"
                suffix += 1
        return base

    def tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for exposed_name, (connection, tool_name, server_tool) in self.tools.items():
            input_schema = server_tool.get("inputSchema")
            description = server_tool.get("description")
            if not isinstance(description, str) or not description.strip():
                description = f"Call the '{tool_name}' tool provided by the '{connection.name}' MCP server."
            schemas.append({
                "type": "function",
                "function": {
                    "name": exposed_name,
                    "description": description,
                    "parameters": (
                        input_schema
                        if isinstance(input_schema, dict)
                        else {"type": "object", "properties": {}}
                    ),
                },
            })
        return schemas

    def find_tool_schemas(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if self.pending_servers and re.search(
            r"\b(chrome|browser|web|pages?|tabs?|navigate|navigation|websites?|url|click|screenshot|snapshot)\b",
            query,
            re.IGNORECASE,
        ):
            for server_name, server_config in list(self.pending_servers.items()):
                try:
                    self._connect_server(server_name, server_config)
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    print(f"[MCP 연결 실패] {server_name}: {error}", file=sys.stderr)
                else:
                    del self.pending_servers[server_name]

        query_terms = {
            term
            for term in re.findall(r"[a-z0-9]+|[\uac00-\ud7a3]+", query.lower())
            if len(term) > 1
        }
        if not query_terms:
            return []

        ranked_tools = []
        for exposed_name, (_, tool_name, server_tool) in self.tools.items():
            description = server_tool.get("description")
            name_terms = set(re.findall(r"[a-z0-9]+|[\uac00-\ud7a3]+", f"{exposed_name} {tool_name}".lower()))
            description_terms = set(re.findall(r"[a-z0-9]+|[\uac00-\ud7a3]+", (description or "").lower()))
            score = sum(
                (5 if term in name_terms else 0) + (1 if term in description_terms else 0)
                for term in query_terms
            )
            if score:
                ranked_tools.append((score, exposed_name))
        ranked_tools.sort(key=lambda item: (-item[0], item[1]))
        selected_names = {name for _, name in ranked_tools[:limit]}
        return [
            schema
            for schema in self.tool_schemas()
            if schema["function"]["name"] in selected_names
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        connection, tool_name, _ = self.tools[name]
        return connection.call_tool(tool_name, arguments)

    def close(self) -> None:
        for connection in self.connections.values():
            connection.close()


def tool_schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def is_responses_model(model: str) -> bool:
    return model.startswith("gpt-6-")


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue

        parts: list[dict[str, Any]] = []
        for part in content:
            if part.get("type") == "text":
                parts.append({"type": "input_text", "text": part["text"]})
            elif part.get("type") == "image_url":
                parts.append({
                    "type": "input_image",
                    "image_url": part["image_url"]["url"],
                    "detail": part["image_url"].get("detail", "auto"),
                })
        converted.append({"role": role, "content": parts})
    return converted


def responses_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["function"]["name"],
            "description": tool["function"]["description"],
            "parameters": tool["function"]["parameters"],
        }
        for tool in tools
    ]


def request_responses_text(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
) -> str:
    response = client.responses.create(
        model=model,
        input=to_responses_input(messages),
    )
    return response.output_text or ""


TOOLS = [
    tool_schema("todo_list", "List the shared Todo app tasks. Use status all, active, or completed.", {"status": {"type": "string", "enum": ["all", "active", "completed"]}}, []),
    tool_schema("todo_add", "Add a task to the shared Todo app. The task appears in the web app immediately.", {"title": {"type": "string"}, "description": {"type": "string"}, "due_date": {"type": "string", "description": "Optional date in YYYY-MM-DD format."}, "priority": {"type": "string", "enum": ["low", "medium", "high"]}}, ["title"]),
    tool_schema("todo_update", "Update a task in the shared Todo app. Set only the fields that should change; completed is true or false.", {"task_id": {"type": "integer"}, "title": {"type": "string"}, "description": {"type": "string"}, "due_date": {"type": ["string", "null"], "description": "Date in YYYY-MM-DD format, or null to clear it."}, "priority": {"type": "string", "enum": ["low", "medium", "high"]}, "completed": {"type": "boolean"}}, ["task_id"]),
    tool_schema("todo_delete", "Permanently delete a task from the shared Todo app.", {"task_id": {"type": "integer"}}, ["task_id"]),
    tool_schema("execute_cmd", "Run a system command in the current working directory. Use cd to change directories.", {"command": {"type": "string"}}, ["command"]),
    tool_schema("rag_index_documents", "Index documents in a workspace directory for retrieval-augmented generation. Run before searching documents.", {"directory": {"type": "string"}}, []),
    tool_schema("rag_search", "Search indexed workspace documents for content relevant to a question. Use before answering questions based on workspace documents.", {"query": {"type": "string"}, "top_k": {"type": "integer"}}, ["query"]),
    tool_schema("web_search", "Search the public web independently of the controlled Chrome session and return up to five DuckDuckGo results.", {"query": {"type": "string"}}, ["query"]),
    tool_schema("web_navigate", "Navigate Quasar's controlled Chrome browser directly to an http or https URL and report the resulting page title and URL.", {"url": {"type": "string"}}, ["url"]),
    tool_schema("web_click", "Click an element on the current page using a CSS selector.", {"css_selector": {"type": "string"}}, ["css_selector"]),
    tool_schema("web_input", "Enter text into a web input selected by CSS selector and optionally press Enter.", {"css_selector": {"type": "string"}, "text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["css_selector", "text"]),
    tool_schema("web_scroll", "Scroll the current Chrome page up or down.", {"direction": {"type": "string", "enum": ["up", "down"]}}, ["direction"]),
    tool_schema("web_back", "Navigate Chrome to the previous page.", {}, []),
    tool_schema("web_key_press", "Type text or press a key in the currently focused web element. Example keys: ENTER, TAB, ESCAPE.", {"key": {"type": "string"}, "text": {"type": "string"}}, ["key"]),
    tool_schema("gmail_open", "Open the Gmail inbox in Chrome.", {}, []),
    tool_schema("gmail_search", "Search Gmail for the specified query.", {"query": {"type": "string"}}, ["query"]),
    tool_schema("gmail_create_draft", "Compose and save a Gmail draft without sending it.", {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"]),
    tool_schema("gmail_send", "Send an email through the signed-in Gmail account in Quasar's dedicated Chrome only when the user explicitly requests sending. If not signed in, open Chrome with /gmail and ask the user to sign in. Do not retry sending without the user's confirmation.", {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"]),
    tool_schema("get_current_time", "Get the current date and time.", {}, []),
    tool_schema("create_calendar_event", "Create and open an .ics calendar event file.", {"title": {"type": "string"}, "date": {"type": "string", "description": "Date in YYYYMMDD format."}, "time": {"type": "string", "description": "Time in HHMMSS format."}, "description": {"type": "string"}}, ["title", "date", "time"]),
    tool_schema("schedule_command", "Schedule a one-time command to run in the current working directory at a future date and time.", {"target_datetime": {"type": "string", "description": "Date and time in YYYY-MM-DD HH:MM format."}, "command": {"type": "string"}}, ["target_datetime", "command"]),
    tool_schema("set_timer", "Set a timer for the specified number of seconds.", {"seconds": {"type": "integer"}, "message": {"type": "string"}}, ["seconds", "message"]),
    tool_schema("set_alarm", "Set an alarm for a specific time in HH:MM format.", {"target_time": {"type": "string"}, "message": {"type": "string"}}, ["target_time", "message"]),
    tool_schema("capture_screen", "Capture the current screen for visual analysis.", {}, []),
    tool_schema("vscode_open", "Open a file or directory inside the workspace in VS Code.", {"path": {"type": "string"}}, ["path"]),
    tool_schema("python_list_files", "List up to 100 Python files in a workspace directory.", {"directory": {"type": "string"}}, []),
    tool_schema("python_read_file", "Read a Python file inside the workspace.", {"file_path": {"type": "string"}}, ["file_path"]),
    tool_schema("read_workspace_file", "Read the full contents of a text file inside the workspace. Read existing files before modifying them in any language.", {"file_path": {"type": "string"}}, ["file_path"]),
    tool_schema("write_workspace_file", "Create or update a text file inside the workspace. Before updating, pass the exact contents read with read_workspace_file as expected_source to detect conflicts. Python files are syntax-checked before saving. Create a new file only when explicitly requested, with create=true and an empty expected_source.", {"file_path": {"type": "string"}, "source": {"type": "string"}, "expected_source": {"type": "string"}, "create": {"type": "boolean"}}, ["file_path", "source", "expected_source"]),
    tool_schema("python_check_syntax", "Check the syntax of a Python file in the workspace without running it.", {"file_path": {"type": "string"}}, ["file_path"]),
    tool_schema("python_run_file", "Run a Python file in the workspace when execution is enabled, with a 30-second timeout.", {"file_path": {"type": "string"}}, ["file_path"]),
    tool_schema("discover_mcp_tools", "Search connected MCP tools when a request needs an integration not covered by the available tools. Provide an English query describing the required capability. Matching tool schemas will be available in the next step.", {"query": {"type": "string", "description": "English keywords for the needed tool or capability."}}, ["query"]),
    tool_schema("end", "Immediately finish the current agent task and return final_response to the user. Call when the task is complete or blocked; no further tools will run for this task.", {"final_response": {"type": "string", "description": "The concise final result, including any remaining blocker."}}, ["final_response"]),
]


def run_tool(
    name: str,
    arguments: dict[str, Any],
    live_analysis_server: CliLiveAnalysisServer | None = None,
    mcp_manager: MCPServerManager | None = None,
) -> str | tuple[str, int, int]:
    if mcp_manager is not None and name in mcp_manager.tools:
        try:
            return mcp_manager.call_tool(name, arguments)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            return f"MCP 도구 실행 실패 ({name}): {error}"
    if name.startswith("todo_"):
        try:
            if name == "todo_list":
                tasks = todo_store.list_tasks(arguments.get("status", "all"))
                return json.dumps(tasks, ensure_ascii=False, indent=2)
            if name == "todo_add":
                task = todo_store.add_task(
                    arguments.get("title"),
                    arguments.get("description", ""),
                    arguments.get("due_date"),
                    arguments.get("priority", "medium"),
                )
                return f"할 일을 추가했습니다:\n{json.dumps(task, ensure_ascii=False, indent=2)}"
            if name == "todo_update":
                task_id = arguments.get("task_id")
                changes = {
                    field: arguments[field]
                    for field in ("title", "description", "due_date", "priority", "completed")
                    if field in arguments
                }
                task = todo_store.update_task(task_id, changes)
                return f"할 일을 수정했습니다:\n{json.dumps(task, ensure_ascii=False, indent=2)}"
            if name == "todo_delete":
                task_id = arguments.get("task_id")
                todo_store.delete_task(task_id)
                return f"할 일 (ID: {task_id})을 삭제했습니다."
        except (KeyError, OSError, sqlite3.Error, ValueError) as error:
            return f"할 일 처리 실패: {error}"
        return f"알 수 없는 할 일 도구: {name}"
    if name == "execute_cmd":
        command = arguments.get("command", "")
        if not auto_approve_commands:
            approval = input(f"\n명령 실행 승인 [{current_directory}]> {command}\n실행할까요? [y/N]: ").strip().lower()
            if approval not in {"y", "yes"}:
                return "CMD 명령어 실행 승인이 거부되었습니다."
        return execute_cmd(command)
    if name == "rag_index_documents":
        return rag_index_documents(arguments.get("directory", "."))
    if name == "rag_search":
        return rag_search(arguments["query"], arguments.get("top_k", 5))
    if name == "web_search":
        return web_search(arguments["query"])
    if name == "web_navigate":
        return web_navigate(arguments["url"])
    if name == "web_click":
        return web_click(arguments["css_selector"])
    if name == "web_input":
        return web_input(arguments["css_selector"], arguments["text"], arguments.get("press_enter", False))
    if name == "web_scroll":
        return web_scroll(arguments.get("direction", "down"))
    if name == "web_back":
        return web_back()
    if name == "web_key_press":
        return web_key_press(arguments["key"], arguments.get("text", ""))
    if name == "gmail_open":
        return gmail_open()
    if name == "gmail_search":
        return gmail_search(arguments["query"])
    if name == "gmail_create_draft":
        return gmail_create_draft(arguments["to"], arguments["subject"], arguments["body"])
    if name == "gmail_send":
        return gmail_send(arguments["to"], arguments["subject"], arguments["body"])
    if name == "get_current_time":
        return get_current_time()
    if name == "create_calendar_event":
        return create_calendar_event(arguments["title"], arguments["date"], arguments["time"], arguments.get("description", ""))
    if name == "schedule_command":
        return schedule_command(arguments["target_datetime"], arguments["command"])
    if name == "set_timer":
        return set_timer(arguments["seconds"], arguments["message"])
    if name == "set_alarm":
        return set_alarm(arguments["target_time"], arguments["message"])
    if name == "capture_screen":
        return capture_screen()
    if name == "vscode_open":
        return open_in_vscode(arguments.get("path", "."))
    if name == "python_list_files":
        return python_list_files(arguments.get("directory", "."))
    if name == "python_read_file":
        return python_read_file(arguments["file_path"])
    if name == "read_workspace_file":
        return read_workspace_file(arguments["file_path"])
    if name == "write_workspace_file":
        source = arguments.get("source")
        expected_source = arguments.get("expected_source")
        if not isinstance(source, str) or not isinstance(expected_source, str):
            return "파일 수정 실패: source와 expected_source는 문자열이어야 합니다."
        create = arguments.get("create", False)
        if not isinstance(create, bool):
            return "파일 수정 실패: create는 true 또는 false여야 합니다."
        return write_workspace_file(arguments["file_path"], source, expected_source, create)
    if name == "python_check_syntax":
        return python_check_syntax(arguments["file_path"])
    if name == "python_run_file":
        return python_run_file(arguments["file_path"])
    return f"알 수 없는 도구: {name}"


def print_tool_status(name: str, mcp_manager: MCPServerManager | None) -> None:
    if name == "discover_mcp_tools":
        label = "Searching MCP tools"
    elif mcp_manager is not None and name in mcp_manager.tools:
        connection, original_name, _ = mcp_manager.tools[name]
        label = f"{connection.name}.{original_name}"
    else:
        label = name
    print(f"Using tool: {label}", flush=True)


def _redact_debug_value(key: str, value: Any) -> Any:
    if DEBUG_SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_debug_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_debug_value(key, item) for item in value]
    if isinstance(value, str):
        return DEBUG_INLINE_SECRET_PATTERN.sub(r"\1[REDACTED]", value)
    return value


def print_debug_event(title: str, details: Any) -> None:
    if not debug_mode:
        return
    safe_details = _redact_debug_value("", details)
    rendered = json.dumps(safe_details, ensure_ascii=False, indent=2, default=str)
    if len(rendered) > DEBUG_MAX_RESULT_CHARS:
        rendered = rendered[:DEBUG_MAX_RESULT_CHARS] + "\n... (출력 일부 생략)"
    print(f"\n[DEBUG] {title}\n{rendered}", flush=True)


def _debug_tool_call(name: str, arguments: dict[str, Any]) -> None:
    print_debug_event("모델 도구 호출", {"tool": name, "arguments": arguments})


def _debug_tool_result(name: str, result: Any) -> None:
    sensitive_result_tools = {
        "capture_screen",
        "gmail_search",
        "gmail_create_draft",
        "gmail_send",
        "python_read_file",
        "read_workspace_file",
        "rag_search",
    }
    if name in sensitive_result_tools:
        result = f"[결과 내용 숨김: {len(str(result))}자]"
    print_debug_event("도구 실행 결과", {"tool": name, "result": result})


def print_help() -> None:
    print(
        "\nQuasar CLI 명령:\n"
        "  /help             도움말 표시\n"
        "  /clear            대화 기록 초기화\n"
        "  /cwd              현재 작업 폴더 표시\n"
        "  /cd <경로>        작업 폴더 변경\n"
        "  /approve on|off   명령어 자동 승인 설정\n"
        "  /python-run on|off Python 파일 실행 허용 설정\n"
        "  /debug [on|off]   모델의 도구 호출/명령 인자 디버그 표시\n"
        "  /rag-index [경로]  문서를 RAG 검색용으로 색인\n"
        "  /rag-search <질문> 색인 문서에서 관련 내용 검색\n"
        "  웹 검색은 DuckDuckGo로 Chrome 조작과 별도로 실행됩니다.\n"
        "  /gmail            Quasar 전용 Chrome에서 Gmail 열기/로그인\n"
        "  /vscode           VS Code 실시간 분석 연결 정보 표시\n"
        "  /collab on|off    공동작업 모드 켜기/끄기\n"
        "  /jobs             예약 명령어 확인\n"
        "  할 일은 자연어로 요청해 Quasar가 Todolist와 함께 관리할 수 있습니다.\n"
        "  /exit             종료\n"
        "코드는 자연어로 요청하고 후속 대화를 이어가며 파일에 직접 수정·저장할 수 있습니다.\n"
        "일반 요청은 Quasar 에이전트가 명령 실행, 웹/Gmail, 일정, 알람, VS Code, Python 및 MCP 도구로 처리합니다.\n"
        "MCP 서버는 작업 폴더의 .mcp.json에 설정하거나 --mcp-config로 설정 파일을 지정합니다."
    )


def handle_cli_command(
    command: str,
    messages: list[dict[str, Any]],
    live_analysis_server: CliLiveAnalysisServer | None = None,
    collaboration_monitor: CliCollaborationMonitor | None = None,
) -> bool:
    global current_directory, auto_approve_commands, allow_python_run, debug_mode
    parts = command.strip().split(maxsplit=1)
    name = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if name == "/help":
        print_help()
    elif name == "/clear":
        messages[:] = messages[:1]
        print("대화 기록을 초기화했습니다.")
    elif name == "/cwd":
        print(current_directory)
    elif name == "/cd":
        if not argument:
            print(f"현재 디렉터리: {current_directory}")
        else:
            target = Path(argument.strip('"'))
            if not target.is_absolute():
                target = current_directory / target
            try:
                resolved = target.resolve()
                if not resolved.is_dir():
                    print(f"디렉터리를 찾을 수 없습니다: {resolved}")
                else:
                    current_directory = resolved
                    print(f"현재 디렉터리: {current_directory}")
            except OSError as error:
                print(f"디렉터리 변경 실패: {error}")
    elif name == "/approve" and argument in {"on", "off"}:
        auto_approve_commands = argument == "on"
        print(f"명령어 자동 승인: {'켜짐' if auto_approve_commands else '꺼짐'}")
    elif name == "/python-run" and argument in {"on", "off"}:
        allow_python_run = argument == "on"
        print(f"Python 파일 실행: {'허용' if allow_python_run else '비활성화'}")
    elif name == "/debug":
        if argument not in {"", "on", "off"}:
            print("사용법: /debug [on|off]")
        else:
            if argument:
                debug_mode = argument == "on"
            print(f"디버그 모드: {'켜짐' if debug_mode else '꺼짐'}")
    elif name == "/rag-index":
        print(rag_index_documents(argument or "."))
    elif name == "/rag-search":
        if not argument:
            print("사용법: /rag-search <질문>")
        else:
            print(rag_search(argument))
    elif name == "/gmail":
        print(gmail_open())
    elif name == "/vscode":
        if live_analysis_server is None:
            print("VS Code 실시간 분석 서버가 실행 중이 아닙니다.")
        else:
            print(
                f"Endpoint: {live_analysis_server.endpoint}\n"
                f"Token: {live_analysis_server.token}"
            )
            document = live_analysis_server.latest_document()
            if document:
                age_seconds = max(0, int(time.time() - document["updated_at"]))
                print(
                    f"마지막 수신 파일: {document['path']}\n"
                    f"마지막 동기화: {age_seconds}초 전 "
                    "(수정 적용 전 VS Code가 현재 버퍼와 일치하는지 확인합니다.)"
                )
            else:
                print("아직 VS Code에서 편집 내용을 받지 못했습니다.")
    elif name == "/collab":
        if argument not in {"on", "off"}:
            print("사용법: /collab on|off")
        elif collaboration_monitor is None:
            print("공동작업 모니터를 사용할 수 없습니다.")
        else:
            enabled = argument == "on"
            collaboration_monitor.set_enabled(enabled)
        
            if enabled:
                print(
                    f"공동작업 모드가 켜졌습니다. VS Code의 현재 편집 내용을 "
                    f"{collaboration_monitor.poll_interval}초마다 확인하고, "
                    f"{collaboration_monitor.stable_seconds}초간 변경이 없을 때 잠재적 오류를 검토합니다. "
                    f"{collaboration_monitor.idle_seconds}초 이상 멈춘 경우에는 "
                    "도움이 필요한지 추가로 확인합니다."
                )
            else:
                print("공동작업 모드가 꺼졌습니다.")
    elif name == "/jobs":
        if not scheduled_jobs:
            print("예약된 명령어가 없습니다.")
        for job in scheduled_jobs:
            print(f"{job['target']} | {job['status']} | {job['command']}")
    else:
        print("알 수 없는 CLI 명령입니다. /help로 사용법을 확인하세요.")
    return True


def request_agent(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    user_input: str,
    live_analysis_server: CliLiveAnalysisServer | None = None,
    mcp_manager: MCPServerManager | None = None,
) -> str:
    saved_user_message: dict[str, Any] = {"role": "user", "content": user_input}
    working_user_message = saved_user_message
    current_document = (
        live_analysis_server.latest_document()
        if live_analysis_server is not None
        else None
    )
    current_file_context = ""
    if current_document:
        source = current_document["source"]
        age_seconds = max(0, int(time.time() - current_document["updated_at"]))
        if len(source) > 24_000:
            source = source[:12_000] + "\n# ... 중간 내용 생략 ...\n" + source[-12_000:]
        current_file_context = (
            "\n\n[현재 VS Code 활성 파일 — 현재 작업의 우선 문맥]\n"
            f"파일: {current_document['path']}\n"
            f"마지막 동기화: {age_seconds}초 전. 이 내용은 마지막으로 수신된 스냅샷입니다. "
            "수정 적용 전 VS Code 확장이 현재 버퍼와 일치하는지 확인하며, 일치하지 않으면 적용하지 않습니다.\n"
            "사용자가 현재 작업이나 열린 파일을 묻는 경우 이 내용에 집중하세요. "
            "요청하지 않은 다른 파일을 나열하거나 전체 작업 폴더를 요약하지 마세요. "
            "다른 파일이 필요할 때만 추가로 확인하세요.\n"
            f"```python\n{source}\n```"
        )
    screen_request = is_screen_request(user_input)
    if screen_request:
        screenshot_data, width, height = capture_screen()
        working_user_message = {
            "role": "user",
            "content": [
            {
                "type": "text",
                "text": (
                    f"{user_input}\n현재 캡처 해상도: {width} x {height} 픽셀."
                    f"{current_file_context}"
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_data}", "detail": "high"}},
            ],
        }
    elif current_file_context:
        working_user_message = {
            "role": "user",
            "content": f"{user_input}{current_file_context}",
        }
    working_messages = [*messages, working_user_message]
    if is_responses_model(model):
        return request_agent_with_responses(
            client,
            model,
            messages,
            saved_user_message,
            working_messages,
            screen_request,
            live_analysis_server,
            mcp_manager,
        )

    active_mcp_names: set[str] = set()
    for _ in range(8):
        available_tools = [
            tool for tool in TOOLS
            if tool["function"]["name"] != "discover_mcp_tools"
            or (mcp_manager is not None and mcp_manager.tools)
        ]
        if mcp_manager is not None:
            available_tools.extend(
                schema
                for schema in mcp_manager.tool_schemas()
                if schema["function"]["name"] in active_mcp_names
            )
        print("\nThinking...", flush=True)
        response = client.chat.completions.create(
            model=model,
            messages=working_messages,
            tools=available_tools,
            tool_choice="auto",
        )
        response_message = response.choices[0].message
        if not response_message.tool_calls:
            answer = response_message.content or "응답이 비어 있습니다. 요청을 다시 시도해 주세요."
            print_debug_event("모델 응답 (도구 호출 없음)", answer)
            messages.extend([saved_user_message, {"role": "assistant", "content": answer}])
            return answer

        if response_message.content:
            print_debug_event("모델이 출력한 안내", response_message.content)
        working_messages.append(response_message.model_dump(exclude_none=True))
        screenshot_result = None
        for tool_call in response_message.tool_calls:
            print_tool_status(tool_call.function.name, mcp_manager)
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("도구 인자는 JSON 객체여야 합니다.")
                _debug_tool_call(tool_call.function.name, arguments)
                if tool_call.function.name == "end":
                    final_response = arguments.get("final_response")
                    if not isinstance(final_response, str) or not final_response.strip():
                        raise ValueError("end 도구에는 비어 있지 않은 final_response가 필요합니다.")
                    messages.extend([
                        saved_user_message,
                        {"role": "assistant", "content": final_response.strip()},
                    ])
                    return final_response.strip()
                if tool_call.function.name == "discover_mcp_tools":
                    if mcp_manager is None:
                        result = "No MCP server is configured."
                    else:
                        query = arguments.get("query")
                        if not isinstance(query, str) or not query.strip():
                            raise ValueError("discover_mcp_tools requires a non-empty query.")
                        discovered = mcp_manager.find_tool_schemas(query)
                        active_mcp_names.update(
                            schema["function"]["name"] for schema in discovered
                        )
                        result = (
                            "MCP tool schemas are now available for the next step: "
                            + ", ".join(schema["function"]["name"] for schema in discovered)
                            if discovered
                            else "No matching MCP tools found. Try a different English query."
                        )
                else:
                    result = run_tool(
                        tool_call.function.name,
                        arguments,
                        live_analysis_server,
                        mcp_manager,
                    )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                result = f"도구 인자 해석 오류: {error}"
            if tool_call.function.name == "capture_screen" and isinstance(result, tuple):
                screenshot_result = result
                image_data, width, height = result
                result = f"현재 화면을 {width} x {height} 픽셀로 캡처했습니다."
            _debug_tool_result(tool_call.function.name, result)
            working_messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": tool_call.function.name,
                "content": result,
            })

        if screenshot_result and not screen_request:
            image_data, width, height = screenshot_result
            working_messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": f"방금 캡처한 화면({width} x {height} 픽셀)을 분석해 사용자 요청에 답변하세요."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}", "detail": "high"}},
                ],
            })

    print("\nThinking...", flush=True)
    response = client.chat.completions.create(model=model, messages=working_messages)
    answer = response.choices[0].message.content or "도구 실행 횟수 제한에 도달했습니다. 요청을 나누어 다시 시도해 주세요."
    print_debug_event("도구 반복 한도 후 모델 응답", answer)
    messages.extend([saved_user_message, {"role": "assistant", "content": answer}])
    return answer


def request_agent_with_responses(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    saved_user_message: dict[str, Any],
    working_messages: list[dict[str, Any]],
    screen_request: bool,
    live_analysis_server: CliLiveAnalysisServer | None = None,
    mcp_manager: MCPServerManager | None = None,
) -> str:
    response_input = to_responses_input(working_messages)
    active_mcp_names: set[str] = set()
    for _ in range(8):
        available_tools = [
            tool for tool in TOOLS
            if tool["function"]["name"] != "discover_mcp_tools"
            or (mcp_manager is not None and mcp_manager.tools)
        ]
        if mcp_manager is not None:
            available_tools.extend(
                schema
                for schema in mcp_manager.tool_schemas()
                if schema["function"]["name"] in active_mcp_names
            )
        print("\nThinking...", flush=True)
        response = client.responses.create(
            model=model,
            input=response_input,
            tools=responses_tool_schemas(available_tools),
            tool_choice="auto",
        )
        function_calls = [
            item for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]
        if not function_calls:
            answer = response.output_text or "응답이 비어 있습니다. 요청을 다시 시도해 주세요."
            print_debug_event("모델 응답 (도구 호출 없음)", answer)
            messages.extend([saved_user_message, {"role": "assistant", "content": answer}])
            return answer

        if response.output_text:
            print_debug_event("모델이 출력한 안내", response.output_text)
        response_input.extend(
            item.model_dump(exclude_none=True)
            for item in response.output
        )
        screenshot_result = None
        for tool_call in function_calls:
            print_tool_status(tool_call.name, mcp_manager)
            try:
                arguments = json.loads(tool_call.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("도구 인자는 JSON 객체여야 합니다.")
                _debug_tool_call(tool_call.name, arguments)
                if tool_call.name == "end":
                    final_response = arguments.get("final_response")
                    if not isinstance(final_response, str) or not final_response.strip():
                        raise ValueError("end 도구에는 비어 있지 않은 final_response가 필요합니다.")
                    messages.extend([
                        saved_user_message,
                        {"role": "assistant", "content": final_response.strip()},
                    ])
                    return final_response.strip()
                if tool_call.name == "discover_mcp_tools":
                    if mcp_manager is None:
                        result = "No MCP server is configured."
                    else:
                        query = arguments.get("query")
                        if not isinstance(query, str) or not query.strip():
                            raise ValueError("discover_mcp_tools requires a non-empty query.")
                        discovered = mcp_manager.find_tool_schemas(query)
                        active_mcp_names.update(
                            schema["function"]["name"] for schema in discovered
                        )
                        result = (
                            "MCP tool schemas are now available for the next step: "
                            + ", ".join(schema["function"]["name"] for schema in discovered)
                            if discovered
                            else "No matching MCP tools found. Try a different English query."
                        )
                else:
                    result = run_tool(
                        tool_call.name,
                        arguments,
                        live_analysis_server,
                        mcp_manager,
                    )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                result = f"도구 인자 해석 오류: {error}"

            if tool_call.name == "capture_screen" and isinstance(result, tuple):
                screenshot_result = result
                image_data, width, height = result
                result = f"현재 화면을 {width} x {height} 픽셀로 캡처했습니다."
            _debug_tool_result(tool_call.name, result)
            response_input.append({
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": result,
            })

        if screenshot_result and not screen_request:
            image_data, width, height = screenshot_result
            response_input.append({
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"방금 캡처한 화면({width} x {height} 픽셀)을 분석해 사용자 요청에 답변하세요.",
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_data}",
                        "detail": "high",
                    },
                ],
            })

    print("\nThinking...", flush=True)
    response = client.responses.create(model=model, input=response_input)
    answer = response.output_text or "도구 실행 횟수 제한에 도달했습니다. 요청을 나누어 다시 시도해 주세요."
    print_debug_event("도구 반복 한도 후 모델 응답", answer)
    messages.extend([saved_user_message, {"role": "assistant", "content": answer}])
    return answer


def append_pending_collaboration_questions(
    messages: list[dict[str, Any]],
    collaboration_monitor: CliCollaborationMonitor,
) -> None:
    for question in collaboration_monitor.take_pending_questions():
        messages.append({"role": "assistant", "content": question})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Quasar 업무 자동화 CLI 에이전트")
    parser.add_argument("--provider", choices=("ollama", "openai"), help="AI 공급자 (기본값: AI_PROVIDER 또는 ollama)")
    parser.add_argument("--model", help="공급자 모델 이름")
    parser.add_argument("--no-model-prompt", action="store_true", help="시작 시 공급자/모델 선택을 건너뜀")
    parser.add_argument("--cwd", help="시작 작업 디렉터리")
    parser.add_argument("--confirm-commands", action="store_true", help="각 시스템 명령 실행 전에 확인")
    parser.add_argument("--mcp-config", help="MCP 서버 설정 JSON 파일 (기본값: .mcp.json)")
    args = parser.parse_args(argv)

    global current_directory, auto_approve_commands
    if args.cwd:
        target_directory = Path(args.cwd).expanduser().resolve()
        if not target_directory.is_dir():
            parser.error(f"작업 디렉터리가 아닙니다: {target_directory}")
        current_directory = target_directory
    auto_approve_commands = not args.confirm_commands
    provider = (args.provider or os.getenv("AI_PROVIDER", "ollama")).lower()
    try:
        interactive = sys.stdin.isatty() and not args.no_model_prompt
        if interactive and not args.provider:
            provider = choose_provider(provider)
        client, model = create_client(provider, args.model)
        if interactive and not args.model:
            model = choose_model(client, provider, model)
    except Exception as error:
        parser.error(f"AI 클라이언트 초기화 실패: {error}")

    configured_mcp_path = args.mcp_config or os.getenv("MCP_SERVERS_FILE")
    mcp_config_path = Path(configured_mcp_path).expanduser() if configured_mcp_path else current_directory / ".mcp.json"
    if not mcp_config_path.is_absolute():
        mcp_config_path = current_directory / mcp_config_path
    if configured_mcp_path and not mcp_config_path.is_file():
        parser.error(f"MCP 설정 파일을 찾을 수 없습니다: {mcp_config_path}")
    try:
        mcp_manager = MCPServerManager(mcp_config_path)
    except ValueError as error:
        parser.error(str(error))

    collaboration_config_path = current_directory / "collaboration_config.json"
    try:
        collaboration_settings = load_collaboration_settings(collaboration_config_path)
    except ValueError as error:
        parser.error(str(error))

    system_prompt = (
        "You are Quasar, a business-automation CLI agent. Understand requests and answer clearly in Korean. Use tools when "
        "useful; answer greetings directly. Never expose tool JSON, schemas, or internal routing. Run Python only when enabled. "
        "For workspace documents, search with rag_search (index first with rag_index_documents if needed), cite source paths, "
        "and do not invent facts. Prefer the active VS Code file; do not summarize unrelated workspace content. "
        "For coding, pair program and ask only if ambiguity changes the implementation. Read files with read_workspace_file "
        "before editing; save with write_workspace_file and the exact expected_source. Create files only on explicit request. "
        "Do not use VS Code editor tools for edits, run unrelated commands, or claim unverified results; report conflicts/failures. "
        "Use web_search for independent public-web searches; it must not open or change the controlled Chrome session. "
        "When asked to open or go to a URL, use web_navigate directly and report its actual result; do not substitute a search result link "
        "or claim a browser-control limitation without trying the tool. Use web_click, web_input, web_scroll, and related tools "
        "only to interact with the current Chrome page. "
        "For a requested browser action, call the appropriate browser tool in the same turn before describing it. "
        "Never promise to retry or claim success without a new tool result; if a tool fails, stop and report that failure instead "
        "of saying you will try again. For web_input, inspect the returned result and do not claim text was entered or sent unless verified. "
        "For every agent task, once the requested work is complete, stop all further work and immediately call end with the final result. "
        "If the task is blocked and no safe or useful action remains, immediately call end with the blocker and what was not completed. "
        "Do not continue with unrelated improvements, extra tool calls, or promises of future work after completion. "
        "This ends only the current agent task; keep the CLI session available for the user's next request. "
        "Use rag_search only for indexed workspace documents; it is not web search. Use discover_mcp_tools with an English "
        "capability query only when an integration is needed. For non-code files, call the "
        "matching tool and confirm output/path before claiming success. After all work, call end last with an accurate "
        "final_response, including failures. Keep follow-ups in context; do not re-ask an agreed diagnosis. "
        "Send email only on explicit request; otherwise draft. Gmail uses Quasar's Chrome profile; ask for sign-in via /gmail "
        "if needed. Do not replace browser requests with screenshot analysis; base visual analysis on attached screenshots. "
        "The working directory persists between commands. Manage the shared Todo app with todo_list, todo_add, "
        "todo_update, and todo_delete; the web app and Quasar use the same local SQLite database. Use these tools "
        "for todo requests and confirm their result before reporting a change."
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    try:
        live_analysis_server = CliLiveAnalysisServer(current_directory)
    except OSError as error:
        parser.error(f"VS Code 실시간 분석 서버 시작 실패: {error}")
    collaboration_monitor = CliCollaborationMonitor(
        client,
        model,
        live_analysis_server,
        collaboration_settings,
    )
    print(f"Quasar CLI 시작 ({provider.upper()} / {model}) | 작업 폴더: {current_directory}")
    print("VS Code 연동: 확장에서 'Quasar: Connect Live Analysis' 실행 후 다음 정보를 입력하세요.")
    print(f"Endpoint: {live_analysis_server.endpoint}\nToken: {live_analysis_server.token}")
    if mcp_manager.connections:
        print(
            f"MCP 연동: {len(mcp_manager.connections)}개 서버, "
            f"{len(mcp_manager.tools)}개 도구 ({mcp_config_path})"
        )
    else:
        print(f"MCP 연동: 연결된 서버 없음 ({mcp_config_path})")
    print("종료: /exit 또는 exit | 도움말: /help")
    try:
        while True:
            try:
                user_input = input(f"\n[{current_directory}] 요청: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                break
            if user_input.lower() in {"exit", "quit", "/exit"}:
                print("종료합니다.")
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                handle_cli_command(
                    user_input,
                    messages,
                    live_analysis_server,
                    collaboration_monitor,
                )
                continue
            try:
                append_pending_collaboration_questions(messages, collaboration_monitor)
                answer = request_agent(
                    client,
                    model,
                    messages,
                    user_input,
                    live_analysis_server,
                    mcp_manager,
                )
                print(f"\nQuasar: {answer}")
            except Exception as error:
                print(f"\n요청 처리 실패: {error}", file=sys.stderr)
    finally:
        collaboration_monitor.close()
        live_analysis_server.close()
        mcp_manager.close()
        close_browser()


if __name__ == "__main__":
    main()
