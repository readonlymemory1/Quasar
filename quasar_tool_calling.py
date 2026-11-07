from __future__ import annotations

from typing import Any


LEGACY_MODE = "legacy"
DYNAMIC_MODE = "dynamic"
TOOL_CALLING_MODES = (LEGACY_MODE, DYNAMIC_MODE)

DEFAULT_MAX_TOOL_ROUNDS = 8
MIN_TOOL_ROUNDS = 1
MAX_TOOL_ROUNDS = 32

CATEGORY_DETAILS: dict[str, tuple[str, str, frozenset[str]]] = {
    "code": (
        "코드",
        "Python 파일 목록 조회·읽기, 문법 검사·실행, 코드 파일 생성·수정, 작업 폴더에서 명령 실행. "
        "수정 전 반드시 읽고 "
        "정확한 기존 내용을 expected_source로 전달하세요. 실행 또는 시스템 명령은 승인 후에만 수행합니다.",
        frozenset({
            "python_list_files",
            "python_read_file",
            "python_check_syntax",
            "python_run_file",
            "write_workspace_file",
            "execute_cmd",
        }),
    ),
    "code_analysis": (
        "코드 분석",
        "Grep 정규식 검색, Glob 파일 선택, Python AST 함수·클래스 구조 보기, Git Diff 변경 부분 확인, "
        "VS Code LSP로 정의·참조·호버/타입·진단 조회를 할 수 있습니다. "
        "LSP 기능은 Quasar Live Analysis VS Code 확장 연결이 필요합니다.",
        frozenset({
            "grep",
            "glob",
            "python_ast_outline",
            "git_diff",
            "lsp_query",
        }),
    ),
    "files": (
        "파일",
        "작업 폴더의 파일 탐색·읽기·수정, 문서 검색 색인·검색, VS Code에서 파일 열기. "
        "작업 폴더 밖의 경로는 사용할 수 없습니다.",
        frozenset({
            "python_list_files",
            "python_read_file",
            "read_workspace_file",
            "write_workspace_file",
            "rag_index_documents",
            "rag_search",
            "vscode_open",
        }),
    ),
    "web": (
        "웹",
        "크롬 브라우저를 직접 켜고(실행하고) 닫을 수 있습니다. 공개 웹 검색, "
        "Chrome에서 페이지 열기·탭 전환·탐색, 페이지 클릭·입력·스크롤·키 입력, "
        "화면 캡처, 브라우저 제어 MCP 도구. '크롬 켜줘', '브라우저 열어줘'처럼 "
        "브라우저 자체를 여는 요청도 이 카테고리로 처리합니다. "
        "페이지 조작 전 DOM을 확인하고 결과를 검증하세요.",
        frozenset({
            "web_search",
            "web_navigate",
            "web_open_new_tab",
            "web_list_tabs",
            "web_switch_tab",
            "web_click",
            "web_input",
            "web_scroll",
            "web_back",
            "web_key_press",
            "capture_screen",
        }),
    ),
    "desktop": (
        "데스크톱 제어",
        "사용자가 Quasar 설정에서 정한 10~300초 시간과 Quasar 데스크톱 제어 창에서 "
        "명시적으로 시작한 세션 안에서만 "
        "화면 캡처, 좌표 마우스 이동·왼쪽 클릭, 문자 입력, 제한된 키 입력을 수행합니다. "
        "세션이 꺼져 있으면 먼저 desktop_start_session으로 사용자 승인을 요청하세요. "
        "세션은 40회 동작으로 제한되며 화면 미리보기에서 언제든 중지할 수 있습니다. "
        "화면 캡처는 사용자가 요청했을 때 모델에 전송됩니다. 비밀번호 입력이나 "
        "사용자 확인이 필요한 최종 제출·삭제·구매 등은 수행하지 마세요.",
        frozenset({
            "desktop_start_session",
            "desktop_capture",
            "desktop_move_mouse",
            "desktop_click",
            "desktop_type_text",
            "desktop_press_key",
        }),
    ),
    "email": (
        "이메일",
        "Gmail 열기·검색, 이메일 초안 작성·발송. 이메일 발송은 사용자가 명시적으로 요청하고 "
        "승인한 경우에만 수행하세요.",
        frozenset({
            "gmail_open",
            "gmail_search",
            "gmail_create_draft",
            "gmail_send",
        }),
    ),
    "calendar": (
        "일정·시간",
        "현재 시각 조회, 일정 파일 생성, 알람·타이머 설정, 명령 예약. "
        "예약 명령은 실행 전 승인이 필요합니다.",
        frozenset({
            "get_current_time",
            "create_calendar_event",
            "schedule_command",
            "set_timer",
            "set_alarm",
        }),
    ),
    "tasks": (
        "할 일",
        "공유 할 일 목록 조회, 항목 추가·수정·완료 처리·삭제. 삭제는 실행 전 승인이 필요합니다.",
        frozenset({
            "todo_list",
            "todo_add",
            "todo_update",
            "todo_delete",
        }),
    ),
    "integrations": (
        "외부 연동",
        "MCP 서버 목록 조회, 새 서버 추가·연결, 서버 제거·연결 종료, 연결된 서버의 외부 기능 검색 및 사용. "
        "새 서버를 추가하거나 제거할 때는 사용자 승인을 받아야 합니다. "
        "추가한 서버의 command/args/env 설정은 MCP 설정 파일에 저장됩니다.",
        frozenset({
            "list_mcp_servers",
            "add_mcp_server",
            "remove_mcp_server",
            "discover_mcp_tools",
        }),
    ),
}

_CATEGORY_PURPOSES = {
    "code": "코드를 작성할 때 사용하는 도구입니다.",
    "code_analysis": "코드를 빠르게 탐색하고 구조와 변경 사항을 분석하는 도구입니다.",
    "files": "작업 폴더의 파일과 문서를 다루는 도구입니다.",
    "web": "웹과 연결된 브라우저 제어 MCP를 다루는 도구입니다.",
    "desktop": "활성화된 제한 시간 동안 데스크톱 화면을 보고 기본 입력을 수행합니다.",
    "email": "이메일을 검색하고 작성하는 도구입니다.",
    "calendar": "일정과 시간 관련 작업에 사용하는 도구입니다.",
    "tasks": "할 일 목록을 관리하는 도구입니다.",
    "integrations": "연결된 외부 서비스를 사용하는 도구입니다.",
}


CATEGORY_SELECTION_TOOL = {
    "type": "function",
    "function": {
        "name": "select_tool_categories",
        "description": (
            "요청을 수행하는 데 필요한 도구 카테고리를 하나 이상 선택합니다. "
            "요청의 목표와 단계에 맞는 카테고리를 모두 골라 categories로 전달하세요. "
            "선택한 카테고리의 도구가 제공되며, 작업 중 다른 카테고리가 필요하면 추가로 선택할 수 있습니다.\n"
            "카테고리:\n"
            + "\n".join(
                f"- {category}: {_CATEGORY_PURPOSES[category]} 주요 기능: {details[1]}"
                for category, details in CATEGORY_DETAILS.items()
            )
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(CATEGORY_DETAILS),
                    },
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["categories"],
        },
    },
}


def resolve_tool_calling_mode(configured_value: str | None = None) -> str:
    value = configured_value or DYNAMIC_MODE
    normalized = value.strip().lower()
    if normalized not in TOOL_CALLING_MODES:
        raise ValueError(
            "Tool calling mode must be 'legacy' or 'dynamic'."
        )
    return normalized


def load_categories(
    requested_categories: Any,
    loaded_categories: set[str],
    tools: list[dict[str, Any]],
) -> str:
    if (
        not isinstance(requested_categories, list)
        or not requested_categories
        or not all(isinstance(item, str) for item in requested_categories)
    ):
        return "도구 카테고리 선택 실패: categories는 하나 이상의 카테고리 이름 목록이어야 합니다."

    unknown = sorted(set(requested_categories) - CATEGORY_DETAILS.keys())
    if unknown:
        return f"도구 카테고리 선택 실패: 알 수 없는 카테고리: {', '.join(unknown)}"

    newly_loaded = set(requested_categories) - loaded_categories
    loaded_categories.update(requested_categories)
    available_names = {
        tool["function"]["name"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    }

    sections = []
    for category in requested_categories:
        title, description, tool_names = CATEGORY_DETAILS[category]
        category_tools = sorted(tool_names & available_names)
        sections.append(
            f"{title} ({category})\n"
            f"주요 기능: {_CATEGORY_PURPOSES[category]} {description}\n"
            f"사용 가능한 도구: {', '.join(category_tools) if category_tools else '현재 사용할 수 있는 도구 없음'}"
        )
    newly_loaded_names = ", ".join(sorted(newly_loaded)) or "없음 (이미 선택됨)"
    return (
        f"카테고리 도구 로드 완료: {', '.join(requested_categories)}\n"
        f"이번에 새로 로드됨: {newly_loaded_names}\n\n"
        + "\n\n".join(sections)
    )


def initial_tools(
    tools: list[dict[str, Any]],
    dynamic_entry_names: set[str],
) -> list[dict[str, Any]]:
    available = [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("name") in dynamic_entry_names
    ]
    return [CATEGORY_SELECTION_TOOL, *available]


def tools_for_categories(
    tools: list[dict[str, Any]],
    loaded_categories: set[str],
) -> list[dict[str, Any]]:
    active_names: set[str] = set()
    for category in loaded_categories:
        active_names.update(CATEGORY_DETAILS[category][2])
    return [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("name") in active_names
    ]
