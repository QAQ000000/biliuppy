from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import AppContext

router = APIRouter()
ALLOWED_LOGS = {"ds_update.log", "download.log", "upload.log", "biliup.log"}
TAIL_BLOCK_SIZE = 8192
TAIL_MAX_BYTES = 256 * 1024


def tail_lines(path: Path, count: int = 50) -> list[str]:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        position = stream.tell()
        chunks: list[bytes] = []
        bytes_read = 0
        newlines = 0
        while position > 0 and newlines <= count and bytes_read < TAIL_MAX_BYTES:
            size = min(TAIL_BLOCK_SIZE, position, TAIL_MAX_BYTES - bytes_read)
            position -= size
            stream.seek(position)
            chunk = stream.read(size)
            chunks.append(chunk)
            bytes_read += size
            newlines += chunk.count(b"\n")
    return b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()[-count:]


def read_appended(path: Path, position: int) -> tuple[list[str], int]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(position)
        lines = [line.rstrip("\r\n") for line in stream]
        return lines, stream.tell()


@router.websocket("/v1/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    context: AppContext = websocket.app.state.context
    if context.settings.auth_enabled and not websocket.session.get("user_id"):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    name = websocket.query_params.get("file", "biliup.log")
    if name not in ALLOWED_LOGS:
        await websocket.send_text(f"不允许访问请求的文件: {name}")
        await websocket.close(code=1008)
        return
    path = context.paths.logs / name
    if not path.exists():
        await websocket.send_text(f"日志文件 {name} 不存在")
        await websocket.close()
        return
    lines = await asyncio.to_thread(tail_lines, path)
    for line in lines:
        await websocket.send_text(line)
    position = path.stat().st_size
    try:
        while True:
            await asyncio.sleep(0.5)
            size = path.stat().st_size
            if size < position:
                position = 0
            if size == position:
                continue
            lines, position = await asyncio.to_thread(read_appended, path, position)
            for line in lines:
                await websocket.send_text(line)
    except (WebSocketDisconnect, FileNotFoundError):
        return
