from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ChatHistoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chats (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        messages_json TEXT NOT NULL
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _history_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {"role": message["role"], "content": message["content"]}
            for message in messages
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]

    @staticmethod
    def _title_for(messages: list[dict[str, str]]) -> str:
        for message in messages:
            if message["role"] == "user":
                title = " ".join(message["content"].split())
                if title:
                    return title[:80] + ("…" if len(title) > 80 else "")
        return "새 채팅"

    def save(
        self,
        chat_id: str | None,
        messages: list[dict[str, Any]],
    ) -> str:
        history_messages = self._history_messages(messages)
        if not history_messages:
            if chat_id is None:
                return uuid.uuid4().hex
            return chat_id

        chat_id = chat_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(history_messages, ensure_ascii=False)
        title = self._title_for(history_messages)
        with closing(self._connect()) as connection:
            with connection:
                existing = connection.execute(
                    "SELECT created_at, title FROM chats WHERE id = ?",
                    (chat_id,),
                ).fetchone()
                created_at = existing["created_at"] if existing else now
                if existing and existing["title"] != "새 채팅":
                    title = existing["title"]
                connection.execute(
                    """
                    INSERT INTO chats (id, title, created_at, updated_at, messages_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        updated_at = excluded.updated_at,
                        messages_json = excluded.messages_json
                    """,
                    (chat_id, title, created_at, now, serialized),
                )
        return chat_id

    def list_recent(self, limit: int = 100) -> list[dict[str, str]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM chats
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, chat_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id, title, created_at, updated_at, messages_json "
                "FROM chats WHERE id = ?",
                (chat_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            messages = json.loads(row["messages_json"])
        except json.JSONDecodeError as error:
            raise ValueError(f"저장된 채팅 형식이 올바르지 않습니다: {error}") from error
        if not isinstance(messages, list) or not all(
            isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            for message in messages
        ):
            raise ValueError("저장된 채팅 메시지 형식이 올바르지 않습니다.")
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "messages": messages,
        }
