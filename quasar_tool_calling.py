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
        "코드 탐색, Python 문법 검사, 실행, 코드 수정 도구입니다. 수정 전 반드시 읽고 "
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
    "files": (
        "파일",
        "작업 폴더의 파일 탐색, 읽기, 검색 색인 및 수정 도구입니다. 작업 폴더 밖의 경로는 사용할 수 없습니다.",
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
        "공개 웹 검색과 Quasar 브라우저 조작 도구입니다. 페이지 조작 전 DOM을 확인하고 결과를 검증하세요.",
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
    "email": (
        "이메일",
        "Gmail 검색 및 초안 작성 도구입니다. 이메일 발송은 사용자가 명시적으로 요청하고 승인한 경우에만 수행하세요.",
        frozenset({
            "gmail_open",
            "gmail_search",
            "gmail_create_draft",
            "gmail_send",
        }),
    ),
    "calendar": (
        "일정·시간",
        "현재 시각 조회, 일정 파일 생성, 알람·타이머 및 예약 명령 도구입니다. 예약 명령은 실행 전 승인이 필요합니다.",
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
        "공유 할 일 목록을 조회하고 추가·수정·삭제하는 도구입니다. 삭제는 실행 전 승인이 필요합니다.",
        frozenset({
            "todo_list",
            "todo_add",
            "todo_update",
            "todo_delete",
        }),
    ),
    "integrations": (
        "외부 연동",
        "연결된 MCP 서버의 도구를 검색하고 사용하는 진입점입니다. 필요한 기능을 영어로 검색한 뒤 반환된 도구를 호출하세요.",
        frozenset({"discover_mcp_tools"}),
    ),
}

CATEGORY_NAMES = tuple(CATEGORY_DETAILS)

LIST_CATEGORIES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_tool_categories",
        "description": "List available tool categories and their short descriptions.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LOAD_CATEGORIES_TOOL = {
    "type": "function",
    "function": {
        "name": "load_tool_categories",
        "description": (
            "Load one or more tool categories needed for the task. Already loaded "
            "categories remain available; additional categories can be loaded later."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(CATEGORY_NAMES)},
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["categories"],
        },
    },
}


def resolve_tool_calling_mode(configured_value: str | None = None) -> str:
    value = configured_value or LEGACY_MODE
    normalized = value.strip().lower()
    if normalized not in TOOL_CALLING_MODES:
        raise ValueError(
            "Tool calling mode must be 'legacy' or 'dynamic'."
        )
    return normalized


def get_category_names() -> str:
    return "\n".join(
        f"- {name}: {details[0]} — {details[1]}"
        for name, details in CATEGORY_DETAILS.items()
    )


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
        return "도구 카테고리 로드 실패: categories는 하나 이상의 카테고리 이름 목록이어야 합니다."

    unknown = sorted(set(requested_categories) - CATEGORY_DETAILS.keys())
    if unknown:
        return f"도구 카테고리 로드 실패: 알 수 없는 카테고리: {', '.join(unknown)}"

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
        title, instructions, tool_names = CATEGORY_DETAILS[category]
        category_tools = sorted(tool_names & available_names)
        sections.append(
            f"{title} ({category})\n"
            f"지침: {instructions}\n"
            f"도구: {', '.join(category_tools) if category_tools else '현재 사용할 수 있는 도구 없음'}"
        )
    newly_loaded_names = ", ".join(sorted(newly_loaded)) or "없음 (이미 로드됨)"
    return (
        f"카테고리 로드 완료: {', '.join(requested_categories)}\n"
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
    return [LIST_CATEGORIES_TOOL, LOAD_CATEGORIES_TOOL, *available]


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
