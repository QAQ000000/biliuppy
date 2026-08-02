from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..context import AppContext
from ..dependencies import get_context

router = APIRouter()
ALLOWED_LOGS = {"ds_update.log", "download.log", "upload.log", "biliup.log"}
TAIL_BLOCK_SIZE = 8192
TAIL_MAX_BYTES = 2 * 1024 * 1024
CATEGORY_SCAN_LINES = 5000
CATEGORY_HISTORY_LINES = 200
LOG_LINE_START = re.compile(r"^\d{4}-\d{2}-\d{2}\s")
RECORDING_LOG = re.compile(
    r"\bbiliup\.(?:recorder|engine\.sync_downloader)\b|"
    r"\bbiliup\.scheduler\b.*(?:record|download|ffmpeg|danmaku|fragment)|"
    r"录制|下载器|ffmpeg|stream url",
    re.IGNORECASE,
)
UPLOAD_LOG = re.compile(
    r"\bbiliup\.(?:uploader|engine\.bili_web_sync)\b|"
    r"\bbiliup\.(?:jobs|scheduler)\b.*\bupload\b|"
    r"上传|投稿|\bupload(?:ed|ing)?\b|preupload|upos|"
    r"protocol=(?:cos|kodo|upos)|线路选择",
    re.IGNORECASE,
)
LOG_CATEGORIES = {"recording", "upload"}


@router.delete("/v1/logs")
def clear_logs(context: AppContext = Depends(get_context)) -> dict[str, int | bool]:
    handler = context.log_handler
    path = Path(handler.baseFilename)
    removed_backups = 0
    handler.acquire()
    try:
        if handler.stream is None:
            path.write_text("", encoding="utf-8")
            handler.stream = handler._open()
        else:
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate(0)
            handler.stream.flush()
        for index in range(1, handler.backupCount + 1):
            backup = Path(f"{path}.{index}")
            if backup.is_file():
                backup.unlink()
                removed_backups += 1
    finally:
        handler.release()
    return {"cleared": True, "removed_backups": removed_backups}


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


def filter_log_lines(lines: list[str], category: str, previous: str = "other") -> tuple[list[str], str]:
    selected: list[str] = []
    current = previous
    for line in lines:
        detected = "upload" if UPLOAD_LOG.search(line) else "recording" if RECORDING_LOG.search(line) else "other"
        if detected != "other" or LOG_LINE_START.match(line):
            current = detected
        if current == category:
            selected.append(line)
    return selected, current


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
    category = websocket.query_params.get("category")
    if category is not None and category not in LOG_CATEGORIES:
        await websocket.send_text(f"不支持的日志分类: {category}")
        await websocket.close(code=1008)
        return
    path = context.paths.logs / name
    if not path.exists():
        await websocket.send_text(f"日志文件 {name} 不存在")
        await websocket.close()
        return
    count = CATEGORY_SCAN_LINES if category else 50
    lines = await asyncio.to_thread(tail_lines, path, count)
    current_category = "other"
    if category:
        lines, current_category = filter_log_lines(lines, category)
        lines = lines[-CATEGORY_HISTORY_LINES:]
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
            if category:
                lines, current_category = filter_log_lines(lines, category, current_category)
            for line in lines:
                await websocket.send_text(line)
    except (WebSocketDisconnect, FileNotFoundError):
        return
