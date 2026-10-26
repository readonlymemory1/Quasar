import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from todo_store import todo_store


MAX_REQUEST_BODY = 16_384
PAGE_PATH = Path(__file__).with_name("todolist.html")


class TodoRequestHandler(BaseHTTPRequestHandler):
    server_version = "TodoList/1.0"

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("요청 본문 크기가 올바르지 않습니다.") from error
        if length < 1 or length > MAX_REQUEST_BODY:
            raise ValueError("요청 본문은 1바이트 이상 16KB 이하여야 합니다.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("올바른 JSON 요청이 아닙니다.") from error
        if not isinstance(payload, dict):
            raise ValueError("요청 본문은 JSON 객체여야 합니다.")
        return payload

    def _task_id(self, path: str) -> int:
        task_id = path.removeprefix("/api/todos/")
        if not task_id or "/" in task_id:
            raise ValueError("할 일 ID가 올바르지 않습니다.")
        return int(task_id)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            try:
                body = PAGE_PATH.read_bytes()
            except OSError as error:
                self.send_error(500, f"화면 파일을 읽지 못했습니다: {error}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path != "/api/todos":
            self._send_json(404, {"error": "요청한 경로를 찾을 수 없습니다."})
            return
        query = parse_qs(parsed.query)
        status = query.get("status", ["all"])[0]
        try:
            self._send_json(200, {"tasks": todo_store.list_tasks(status)})
        except ValueError as error:
            self._send_json(400, {"error": str(error)})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/todos":
            self._send_json(404, {"error": "요청한 경로를 찾을 수 없습니다."})
            return
        try:
            payload = self._read_json()
            task = todo_store.add_task(
                payload.get("title"),
                payload.get("description", ""),
                payload.get("due_date"),
                payload.get("priority", "medium"),
            )
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        self._send_json(201, {"task": task})

    def do_PATCH(self) -> None:
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/todos/"):
            self._send_json(404, {"error": "요청한 경로를 찾을 수 없습니다."})
            return
        try:
            task_id = self._task_id(parsed.path)
            payload = self._read_json()
            task = todo_store.update_task(task_id, payload)
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        except KeyError as error:
            self._send_json(404, {"error": str(error).strip("'")})
            return
        self._send_json(200, {"task": task})

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/todos/"):
            self._send_json(404, {"error": "요청한 경로를 찾을 수 없습니다."})
            return
        try:
            task_id = self._task_id(parsed.path)
            todo_store.delete_task(task_id)
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        except KeyError as error:
            self._send_json(404, {"error": str(error).strip("'")})
            return
        self._send_json(200, {"deleted": True})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="로컬 할 일 목록 앱")
    parser.add_argument("--host", default="127.0.0.1", help="서버 주소 (기본값: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="서버 포트 (기본값: 8765)")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("개인 할 일 데이터 보호를 위해 로컬 주소에서만 실행할 수 있습니다.")
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1부터 65535 사이여야 합니다.")

    try:
        server = ThreadingHTTPServer((args.host, args.port), TodoRequestHandler)
    except OSError as error:
        parser.error(f"할 일 앱 서버를 시작하지 못했습니다: {error}")
    url = f"http://{args.host}:{args.port}"
    print(f"할 일 앱 실행 중: {url} (종료: Ctrl+C)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n할 일 앱을 종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
