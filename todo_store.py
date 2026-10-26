import sqlite3
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


_APP_DATA_DIRECTORY = Path(
    os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
) / "QuasarTodo"
DATABASE_PATH = _APP_DATA_DIRECTORY / "todolist.sqlite3"
_LEGACY_DATABASE_PATH = Path(__file__).with_name("todolist.sqlite3")
if not DATABASE_PATH.exists() and _LEGACY_DATABASE_PATH.is_file():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_LEGACY_DATABASE_PATH) as legacy_connection:
        with sqlite3.connect(DATABASE_PATH) as current_connection:
            legacy_connection.backup(current_connection)
PRIORITIES = {"low", "medium", "high"}
STATUSES = {"all", "active", "completed"}


class TodoStore:
    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    due_date TEXT,
                    priority TEXT NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('low', 'medium', 'high')),
                    completed INTEGER NOT NULL DEFAULT 0
                        CHECK (completed IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_title(title: Any) -> str:
        if not isinstance(title, str):
            raise ValueError("할 일 제목은 문자열이어야 합니다.")
        title = title.strip()
        if not title:
            raise ValueError("할 일 제목을 입력해 주세요.")
        if len(title) > 200:
            raise ValueError("할 일 제목은 200자 이내로 입력해 주세요.")
        return title

    @staticmethod
    def _validate_description(description: Any) -> str:
        if not isinstance(description, str):
            raise ValueError("설명은 문자열이어야 합니다.")
        if len(description) > 2000:
            raise ValueError("설명은 2000자 이내로 입력해 주세요.")
        return description.strip()

    @staticmethod
    def _validate_due_date(due_date: Any) -> str | None:
        if due_date is None or due_date == "":
            return None
        if not isinstance(due_date, str):
            raise ValueError("마감일은 YYYY-MM-DD 형식이어야 합니다.")
        try:
            return date.fromisoformat(due_date).isoformat()
        except ValueError as error:
            raise ValueError("마감일은 YYYY-MM-DD 형식으로 입력해 주세요.") from error

    @staticmethod
    def _validate_priority(priority: Any) -> str:
        if not isinstance(priority, str) or priority not in PRIORITIES:
            raise ValueError("우선순위는 low, medium, high 중 하나여야 합니다.")
        return priority

    @staticmethod
    def _validate_task_id(task_id: Any) -> int:
        if isinstance(task_id, bool):
            raise ValueError("할 일 ID는 양의 정수여야 합니다.")
        if isinstance(task_id, int):
            parsed_id = task_id
        elif isinstance(task_id, str) and task_id.isdecimal():
            parsed_id = int(task_id)
        else:
            raise ValueError("할 일 ID는 양의 정수여야 합니다.")
        if parsed_id < 1:
            raise ValueError("할 일 ID는 양의 정수여야 합니다.")
        return parsed_id

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["completed"] = bool(task["completed"])
        return task

    def list_tasks(self, status: str = "all") -> list[dict[str, Any]]:
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("상태는 all, active, completed 중 하나여야 합니다.")
        query = "SELECT * FROM todos"
        parameters: tuple[int, ...] = ()
        if status == "active":
            query += " WHERE completed = ?"
            parameters = (0,)
        elif status == "completed":
            query += " WHERE completed = ?"
            parameters = (1,)
        query += " ORDER BY completed ASC, due_date IS NULL ASC, due_date ASC, id DESC"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._serialize(row) for row in rows]

    def add_task(
        self,
        title: Any,
        description: Any = "",
        due_date: Any = None,
        priority: Any = "medium",
    ) -> dict[str, Any]:
        title = self._validate_title(title)
        description = self._validate_description(description)
        due_date = self._validate_due_date(due_date)
        priority = self._validate_priority(priority)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO todos (title, description, due_date, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, description, due_date, priority, now, now),
            )
            row = connection.execute(
                "SELECT * FROM todos WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        if row is None:
            raise RuntimeError("저장한 할 일을 다시 읽지 못했습니다.")
        return self._serialize(row)

    def update_task(self, task_id: Any, changes: dict[str, Any]) -> dict[str, Any]:
        task_id = self._validate_task_id(task_id)
        allowed_fields = {"title", "description", "due_date", "priority", "completed"}
        if not isinstance(changes, dict) or not changes:
            raise ValueError("수정할 항목을 하나 이상 지정해 주세요.")
        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"수정할 수 없는 항목입니다: {', '.join(sorted(unknown_fields))}")

        values = dict(changes)
        if "title" in values:
            values["title"] = self._validate_title(values["title"])
        if "description" in values:
            values["description"] = self._validate_description(values["description"])
        if "due_date" in values:
            values["due_date"] = self._validate_due_date(values["due_date"])
        if "priority" in values:
            values["priority"] = self._validate_priority(values["priority"])
        if "completed" in values and not isinstance(values["completed"], bool):
            raise ValueError("완료 상태는 true 또는 false여야 합니다.")

        assignments = [f"{field} = ?" for field in values]
        parameters = [
            int(value) if field == "completed" else value
            for field, value in values.items()
        ]
        assignments.append("updated_at = ?")
        parameters.extend(
            [datetime.now().astimezone().isoformat(timespec="seconds"), task_id]
        )
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE todos SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"할 일을 찾을 수 없습니다 (ID: {task_id}).")
            row = connection.execute(
                "SELECT * FROM todos WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("수정한 할 일을 다시 읽지 못했습니다.")
        return self._serialize(row)

    def delete_task(self, task_id: Any) -> None:
        task_id = self._validate_task_id(task_id)
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM todos WHERE id = ?", (task_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"할 일을 찾을 수 없습니다 (ID: {task_id}).")


todo_store = TodoStore()
