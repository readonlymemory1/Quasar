import http.server
import socketserver
import threading
import os

PORT = 8000
Handler = http.server.SimpleHTTPRequestHandler

def run_server():
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at port {PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    # 서버를 별도 스레드에서 실행하여 메인 프로세스가 유지되도록 함
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("Server started. Press Ctrl+C to stop.")
    # 테스트를 위해 잠시 대기하거나 유지
    import time
    while True:
        time.sleep(1)
