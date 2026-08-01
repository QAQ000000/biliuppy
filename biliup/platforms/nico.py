import asyncio
import random
import re

import biliup.common.util
from biliup.config import config
from ..engine.decorators import Plugin
from ..engine.download import DownloadBase
from . import logger


@Plugin.download(regexp=r'(?:https?://)?(?:(?:www|m|live)\.)?nicovideo\.jp')
class Nico(DownloadBase):
    def __init__(self, fname, url, suffix='flv'):
        super().__init__(fname, url, suffix)
        self.proc = None

    async def acheck_stream(self, is_check=False):
        try:
            response = await biliup.common.util.client.get(self.url, timeout=5)
            # 正则表达式
            pattern = r'"name":"(.*?)","description":"(.*?)"'
            # 执行匹配
            matches = re.findall(pattern, response.text)[0]
            self.room_title = matches[0]
        except:
            logger.info("获取标题失败")
        port = random.randint(1025, 65535)
        stream_shell = [
            "streamlink",
            "--player-external-http",  # 为外部程序提供流媒体数据
            "--player-external-http-port", str(port),  # 对外部输出流的端口
            self.url, "best"  # 流链接
        ]
        if config.get('user', {}).get('niconico-email') is not None:
            stream_shell[1:1] = ["--niconico-email", config.get('user', {}).get('niconico-email')]
        if config.get('user', {}).get('niconico-password') is not None:
            stream_shell[1:1] = ["--niconico-password", config.get('user', {}).get('niconico-password')]
        if config.get('user', {}).get('niconico-user-session') is not None:
            stream_shell[1:1] = ["--niconico-user-session", config.get('user', {}).get('niconico-user-session')]
        if config.get('user', {}).get('niconico-purge-credentials') is not None:
            stream_shell[1:1] = [
                "--niconico-purge-credentials",
                config.get('user', {}).get('niconico-purge-credentials'),
            ]
        self.proc = await asyncio.create_subprocess_exec(*stream_shell)
        self.raw_stream_url = f"http://localhost:{port}"
        i = 0
        while i < 5:
            if self.proc.returncode is not None:
                return False
            await asyncio.sleep(1)
            i += 1
        return True

    def close(self):
        try:
            if self.proc is not None:
                self.proc.terminate()
        except:
            logger.exception(f'terminate {self.fname} failed')
