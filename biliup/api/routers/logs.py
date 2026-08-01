from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import AppContext

router = APIRouter()
ALLOWED_LOGS = {"ds_update.log", "download.log", "upload.log", "biliup.log"}


@router.websocket("/v1/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await websocket.accept()
    context: AppContext = websocket.app.state.context
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
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
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
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(position)
                for line in stream:
                    await websocket.send_text(line.rstrip("\r\n"))
                position = stream.tell()
    except (WebSocketDisconnect, FileNotFoundError):
        return
