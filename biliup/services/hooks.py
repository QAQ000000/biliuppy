from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx
import psutil

from biliup.core import AppPaths

logger = logging.getLogger("biliup.hooks")


def _terminate_process_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = parent.children(recursive=True) + [parent]
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


class HookRunner:
    def __init__(self, paths: AppPaths, *, timeout: float = 300):
        self.paths = paths
        self.timeout = max(0.1, timeout)

    @staticmethod
    def _normalize(step: Any) -> tuple[str | None, Any]:
        if step == "rm":
            return "rm", None
        if not isinstance(step, dict):
            return None, None
        if step.get("cmd"):
            return str(step["cmd"]), step.get("value")
        for operation in ("run", "mv", "rm", "webhook"):
            if operation in step:
                return operation, step.get(operation)
        return None, None

    async def run_commands(self, steps: list[Any] | None, payload: dict[str, Any]) -> None:
        for step in steps or []:
            operation, value = self._normalize(step)
            if operation != "run" or not value:
                continue
            process = await asyncio.create_subprocess_shell(
                str(value),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(json.dumps(payload, ensure_ascii=False).encode()),
                    timeout=self.timeout,
                )
            except TimeoutError:
                await asyncio.to_thread(_terminate_process_tree, process.pid)
                await process.wait()
                raise TimeoutError(f"Hook timed out after {self.timeout:g} seconds: {value}") from None
            if process.returncode:
                raise RuntimeError(f"Hook failed ({process.returncode}): {output.decode(errors='replace')}")

    async def run_postprocessors(
        self,
        steps: list[Any] | None,
        files: list[Path],
        payload: dict[str, Any],
    ) -> None:
        if not steps:
            return
        for step in steps:
            operation, value = self._normalize(step)
            if operation == "rm":
                for file in files:
                    file.unlink(missing_ok=True)
                    file.with_suffix(".xml").unlink(missing_ok=True)
                continue
            if operation == "mv" and value:
                target = self.paths.resolve_user_path(str(value))
                target.mkdir(parents=True, exist_ok=True)
                for file in files:
                    await asyncio.to_thread(shutil.move, str(file), str(target / file.name))
                    subtitle = file.with_suffix(".xml")
                    if subtitle.is_file():
                        await asyncio.to_thread(shutil.move, str(subtitle), str(target / subtitle.name))
                continue
            if operation == "webhook" and value:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(str(value), json=payload)
                    response.raise_for_status()
                if response.text.strip() != "success":
                    raise RuntimeError(f"Webhook did not return success: {response.text[:200]}")
                continue
            await self.run_commands([step], payload)
