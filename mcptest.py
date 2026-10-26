
import asyncio
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    command = shutil.which("pptx-mcp-server")

    if not command:
        raise RuntimeError(
            "pptx-mcp-server 실행 파일을 찾지 못했습니다. "
            "설치한 Python 환경을 활성화했는지 확인하세요."
        )

    server_params = StdioServerParameters(
        command=command,
        args=[],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.list_tools()

            print(f"\nMCP 연결 성공! 도구 수: {len(result.tools)}\n")

            for tool in result.tools:
                print(f"- {tool.name}")
                print(f"  설명: {tool.description or '(설명 없음)'}")
                print(f"  입력 스키마: {tool.inputSchema}\n")


if __name__ == "__main__":
    asyncio.run(main())
