import asyncio
import json
import threading
import time
from collections.abc import Iterable

import requests

from biliup.common.util import client
from biliup.config import config
from biliup.danmaku import DanmakuClient
from biliup.engine.status import StreamProbeResult, StreamStatus

from ..engine.decorators import Plugin
from ..engine.download import DownloadBase
from . import logger, match1, wbi

OFFICIAL_API = "https://api.live.bilibili.com"
STREAM_NAME_REGEXP = r"/live-bvc/\d+/(live_[^/\.]+)"
WBI_WEB_LOCATION = "444.8"
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)
BILIBILI_REQUEST_SESSION = requests.Session()
BILIBILI_REQUEST_LOCK = threading.Lock()
BILIBILI_BATCH_LOCKS: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
BILIBILI_BATCH_WAIT_SECONDS = 1.0
BILIBILI_BATCH_MIN_INTERVAL_SECONDS = 30.0
BILIBILI_BATCH_FAILURE_BASE_SECONDS = 30.0
BILIBILI_BATCH_FAILURE_MAX_SECONDS = 300.0
BILIBILI_CONFIGURED_ROOM_IDS: set[str] = set()
BILIBILI_ROOM_STATUS_CACHE: dict[str, dict | None] = {}
BILIBILI_ROOM_STATUS_EXPIRES_AT = 0.0
BILIBILI_BATCH_FAILURE_REASON: str | None = None
BILIBILI_BATCH_FAILURE_COUNT = 0
BILIBILI_BATCH_RETRY_AT = 0.0
WBI_UPDATE_LOCKS: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


class BilibiliStatusUnavailable(RuntimeError):
    pass


def get_bilibili_batch_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return BILIBILI_BATCH_LOCKS.setdefault(loop, asyncio.Lock())


def configure_bilibili_rooms(urls: Iterable[str]) -> None:
    global BILIBILI_CONFIGURED_ROOM_IDS, BILIBILI_ROOM_STATUS_EXPIRES_AT
    global BILIBILI_BATCH_FAILURE_REASON, BILIBILI_BATCH_FAILURE_COUNT
    global BILIBILI_BATCH_RETRY_AT
    room_ids = {
        room_id for url in urls
        if (room_id := match1(url, r'bilibili.com/(\d+)'))
    }
    if room_ids != BILIBILI_CONFIGURED_ROOM_IDS:
        BILIBILI_CONFIGURED_ROOM_IDS = room_ids
        BILIBILI_ROOM_STATUS_CACHE.clear()
        BILIBILI_ROOM_STATUS_EXPIRES_AT = 0.0
        BILIBILI_BATCH_FAILURE_REASON = None
        BILIBILI_BATCH_FAILURE_COUNT = 0
        BILIBILI_BATCH_RETRY_AT = 0.0


def _parse_live_status(payload: object, context: str) -> int:
    if not isinstance(payload, dict):
        raise BilibiliStatusUnavailable(f"{context} returned an invalid room status")
    value = payload.get("live_status")
    if type(value) is not int or value not in {0, 1, 2}:
        raise BilibiliStatusUnavailable(
            f"{context} returned invalid live_status {value!r}"
        )
    return value


def _cached_bilibili_room_status(room_id: str) -> dict:
    room_status = BILIBILI_ROOM_STATUS_CACHE[room_id]
    if room_status is None:
        raise BilibiliStatusUnavailable(
            f"Bilibili batch room API omitted room {room_id}"
        )
    _parse_live_status(room_status, f"Bilibili batch room {room_id}")
    return room_status


async def _bilibili_get(url: str, **kwargs):
    """Reuse one serialized requests session to avoid Bilibili fingerprint blocks."""
    kwargs.setdefault("timeout", 15)

    def request():
        # The lock must live inside the worker thread: cancelling to_thread does
        # not stop an in-flight requests call.
        with BILIBILI_REQUEST_LOCK:
            BILIBILI_REQUEST_SESSION.cookies.clear()
            return BILIBILI_REQUEST_SESSION.get(url, **kwargs)

    return await asyncio.to_thread(request)


async def _get_bilibili_room_status(room_id: str) -> dict:
    global BILIBILI_ROOM_STATUS_EXPIRES_AT, BILIBILI_BATCH_FAILURE_REASON
    global BILIBILI_BATCH_FAILURE_COUNT, BILIBILI_BATCH_RETRY_AT
    now = time.monotonic()
    if now < BILIBILI_ROOM_STATUS_EXPIRES_AT and room_id in BILIBILI_ROOM_STATUS_CACHE:
        return _cached_bilibili_room_status(room_id)
    if now < BILIBILI_BATCH_RETRY_AT and BILIBILI_BATCH_FAILURE_REASON:
        raise BilibiliStatusUnavailable(BILIBILI_BATCH_FAILURE_REASON)

    async with get_bilibili_batch_lock():
        now = time.monotonic()
        if now < BILIBILI_ROOM_STATUS_EXPIRES_AT and room_id in BILIBILI_ROOM_STATUS_CACHE:
            return _cached_bilibili_room_status(room_id)
        if now < BILIBILI_BATCH_RETRY_AT and BILIBILI_BATCH_FAILURE_REASON:
            raise BilibiliStatusUnavailable(BILIBILI_BATCH_FAILURE_REASON)

        try:
            await asyncio.sleep(BILIBILI_BATCH_WAIT_SECONDS)
            room_ids = sorted(BILIBILI_CONFIGURED_ROOM_IDS | {room_id})
            params = [("room_ids", value) for value in room_ids]
            params.append(("req_biz", "web_room_componet"))
            response = await _bilibili_get(
                f"{OFFICIAL_API}/xlive/web-room/v1/index/getRoomBaseInfo",
                params=params,
                headers={"user-agent": BILIBILI_USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) and payload.get("code") == 0 else None
            by_room_ids = data.get("by_room_ids") if isinstance(data, dict) else None
            if not isinstance(by_room_ids, dict):
                code = payload.get("code") if isinstance(payload, dict) else "invalid"
                raise BilibiliStatusUnavailable(
                    f"Bilibili batch room API returned code {code}"
                )
        except Exception as exc:
            BILIBILI_BATCH_FAILURE_COUNT += 1
            cooldown = min(
                BILIBILI_BATCH_FAILURE_BASE_SECONDS
                * (2 ** min(BILIBILI_BATCH_FAILURE_COUNT - 1, 4)),
                BILIBILI_BATCH_FAILURE_MAX_SECONDS,
            )
            BILIBILI_BATCH_RETRY_AT = time.monotonic() + cooldown
            BILIBILI_BATCH_FAILURE_REASON = (
                f"Bilibili batch room API unavailable: {exc}; "
                f"retrying in {cooldown:.0f}s"
            )
            raise BilibiliStatusUnavailable(BILIBILI_BATCH_FAILURE_REASON) from exc

        BILIBILI_ROOM_STATUS_CACHE.clear()
        for key, value in by_room_ids.items():
            BILIBILI_ROOM_STATUS_CACHE[str(key)] = value
            if isinstance(value, dict):
                for alias_key in ("room_id", "short_id"):
                    alias = value.get(alias_key)
                    if alias not in {None, 0, "0"}:
                        BILIBILI_ROOM_STATUS_CACHE[str(alias)] = value
        for configured_room_id in room_ids:
            BILIBILI_ROOM_STATUS_CACHE.setdefault(configured_room_id, None)
        interval = max(
            BILIBILI_BATCH_MIN_INTERVAL_SECONDS,
            float(config.get("event_loop_interval", 30) or 30),
        )
        BILIBILI_ROOM_STATUS_EXPIRES_AT = time.monotonic() + interval
        BILIBILI_BATCH_FAILURE_REASON = None
        BILIBILI_BATCH_FAILURE_COUNT = 0
        BILIBILI_BATCH_RETRY_AT = 0.0
        return _cached_bilibili_room_status(room_id)


def get_wbi_update_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return WBI_UPDATE_LOCKS.setdefault(loop, asyncio.Lock())

@Plugin.download(regexp=r'https?://(b23\.tv|live\.bilibili\.com)')
class Bililive(DownloadBase):
    def __init__(self, fname, url, suffix='flv'):
        super().__init__(fname, url, suffix)
        self.fake_headers['user-agent'] = BILIBILI_USER_AGENT
        self.live_start_time = 0
        self.bilibili_danmaku = config.get('bilibili_danmaku', False)
        self.bilibili_danmaku_detail = config.get('bilibili_danmaku_detail', False)
        self.bilibili_danmaku_raw = config.get('bilibili_danmaku_raw', False)
        self.__real_room_id = None
        self.__login_mid = 0
        self.__anchor_mid = 0
        self.bili_cookie = config.get('user', {}).get('bili_cookie')
        self.bili_cookie_file = config.get('user', {}).get('bili_cookie_file')
        self.bili_qn = int(config.get('bili_qn', 25000))
        self.bili_protocol = config.get('bili_protocol', 'stream')
        self.bili_cdn = config.get('bili_cdn', [])
        self.bili_hls_timeout = config.get('bili_hls_transcode_timeout', 60)
        self.bili_api_list = [
            normalize_url(config.get('bili_liveapi', OFFICIAL_API)).rstrip('/'),
            normalize_url(config.get('bili_fallback_api', OFFICIAL_API)).rstrip('/'),
        ]
        self.bili_anonymous_origin = config.get('bili_anonymous_origin', False)
        self.bili_cdn_fallback = config.get('bili_cdn_fallback', False)
        self.unrecordable_reason = None

    async def acheck_stream(self, is_check=False):
        return (await self.aprobe_stream(is_check=is_check)).status is StreamStatus.LIVE

    async def aprobe_stream(self, is_check=False) -> StreamProbeResult:
        self.unrecordable_reason = None

        if "b23.tv" in self.url:
            try:
                resp = await client.get(self.url, follow_redirects=False)
                if resp.status_code not in {301, 302}:
                    raise Exception("不支持的链接")
                url = str(resp.next_request.url)
                if "live.bilibili" not in url:
                    raise Exception("不支持的链接")
                self.url = url
            except Exception as e:
                logger.error(f"{self.plugin_msg}: {e}")
                return StreamProbeResult.unknown(str(e))

        room_id: str = match1(self.url, r'bilibili.com/(\d+)')
        if self.bili_cookie:
            self.fake_headers['cookie'] = self.bili_cookie
        if self.bili_cookie_file:
            try:
                with open(self.bili_cookie_file, encoding='utf-8') as stream:
                    cookies = json.load(stream)["cookie_info"]["cookies"]
                    cookies_str = ''
                    for i in cookies:
                        cookies_str += f"{i['name']}={i['value']};"
                    self.fake_headers['cookie'] = cookies_str
            except Exception:
                logger.exception("load_cookies error")
        self.fake_headers['referer'] = self.url

        # room_init 不需要 WBI 签名，优先用它过滤离线房间并解析短房间号。
        anonymous_headers = {
            key: value for key, value in self.fake_headers.items()
            if key.lower() != "cookie"
        }
        room_init_errors: list[str] = []
        room_init_apis = list(dict.fromkeys(self.bili_api_list))
        if room_init_apis[0] == OFFICIAL_API:
            try:
                room_status = await _get_bilibili_room_status(room_id)
            except Exception as exc:
                room_init_errors.append(f"{OFFICIAL_API} batch: {exc}")
                room_init_apis = [api for api in room_init_apis if api != OFFICIAL_API]
                if not room_init_apis:
                    return StreamProbeResult.unknown(room_init_errors[-1])
            else:
                if _parse_live_status(room_status, "Bilibili batch room") != 1:
                    logger.debug(f"{self.plugin_msg}: 未开播")
                    self.raw_stream_url = None
                    return StreamProbeResult.offline()

        room_init = None
        resolved_room_init = None
        for api in room_init_apis:
            try:
                response = await _bilibili_get(
                    f"{api}/room/v1/Room/room_init",
                    params={"id": room_id},
                    headers=anonymous_headers,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                room_init_errors.append(f"{api}: {exc}")
                continue
            if not isinstance(payload, dict) or payload.get("code") != 0:
                room_init_errors.append(
                    f"{api}: code {payload.get('code') if isinstance(payload, dict) else 'invalid'}"
                )
                continue
            room_init = payload.get("data")
            try:
                live_status = _parse_live_status(room_init, f"{api} room_init")
            except BilibiliStatusUnavailable as exc:
                room_init_errors.append(str(exc))
                continue
            if live_status != 1:
                logger.debug(f"{self.plugin_msg}: 未开播")
                self.raw_stream_url = None
                return StreamProbeResult.offline()
            room_id = str(room_init.get("room_id") or room_id)
            resolved_room_init = room_init
            break
        else:
            logger.warning(
                f"{self.plugin_msg}: room_init failed; falling back to detailed API: "
                + "; ".join(room_init_errors)
            )

        try:
            if int(time.time()) - wbi.last_update >= wbi.UPDATE_INTERVAL:
                async with get_wbi_update_lock():
                    if int(time.time()) - wbi.last_update >= wbi.UPDATE_INTERVAL:
                        await self.update_wbi()
        except Exception as exc:
            return StreamProbeResult.unknown(f"Bilibili WBI update failed: {type(exc).__name__}")

        # 获取直播状态与房间标题。业务错误和网络故障都会尝试备用 API。
        params = {
            "room_id": room_id,
            "web_location": WBI_WEB_LOCATION,
        }
        wbi.sign(params)
        room_info = None
        probe_errors = list(room_init_errors)
        for api in room_init_apis:
            try:
                response = await _bilibili_get(
                    f"{api}/xlive/web-room/v1/index/getInfoByRoom",
                    params=params,
                    headers=self.fake_headers,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                probe_errors.append(f"{api}: {exc}")
                logger.warning(f"{self.plugin_msg}: room API failed via {api}: {exc}")
                continue
            if not isinstance(payload, dict):
                probe_errors.append(f"{api}: response was not an object")
                continue
            if payload.get("code") != 0:
                message = f"code {payload.get('code')}: {payload.get('message', '')}"
                probe_errors.append(f"{api}: {message}")
                logger.error(f"{self.plugin_msg}: {payload}")
                continue
            candidate = payload.get("data")
            room = candidate.get("room_info") if isinstance(candidate, dict) else None
            required_live_fields = {"cover", "title", "room_id", "uid", "live_start_time", "special_type"}
            try:
                live_status = _parse_live_status(room, f"{api} room API")
            except BilibiliStatusUnavailable as exc:
                probe_errors.append(str(exc))
                continue
            if live_status == 1 and not required_live_fields.issubset(room):
                probe_errors.append(f"{api}: live response is missing required room fields")
                continue
            room_info = candidate
            break
        if room_info is None:
            return StreamProbeResult.unknown("; ".join(probe_errors) or "Bilibili room API failed")
        room = room_info["room_info"]
        if _parse_live_status(room, "Bilibili room API") != 1:
            logger.debug(f"{self.plugin_msg}: 未开播")
            self.raw_stream_url = None
            return StreamProbeResult.offline()

        self.live_cover_url = room['cover']
        self.room_title = room['title']
        self.__real_room_id = room['room_id']
        self.__anchor_mid = room['uid']
        live_start_time = room['live_start_time']
        special_type = room['special_type'] # 0: 公开直播, 1: 付费直播, 199: 纯净页面
        if isinstance(resolved_room_init, dict) and resolved_room_init.get('encrypted'):
            self.raw_stream_url = None
            return StreamProbeResult.unrecordable("Bilibili live room is password protected")
        if special_type == 1 or (
            isinstance(resolved_room_init, dict)
            and (
                resolved_room_init.get('is_sp') == 1
                or resolved_room_init.get('special_type') == 1
            )
        ):
            self.raw_stream_url = None
            return StreamProbeResult.unrecordable("Bilibili live stream is paid or DRM protected")
        if live_start_time > self.live_start_time:
            self.live_start_time = live_start_time
            is_new_live = True
        else:
            is_new_live = False

        if is_check:
            return StreamProbeResult.live()
        else:
            self.__login_mid = await self.check_login_status()

        # 复用原画 m3u8 流
        if  self.raw_stream_url is not None \
            and ".m3u8" in self.raw_stream_url \
            and self.bili_qn >= 10000 \
            and not is_new_live:
            url = await self.acheck_url_healthy(self.raw_stream_url)
            if url is not None:
                logger.debug(f"{self.plugin_msg}: 复用 {url}")
                return StreamProbeResult.live()
            else:
                self.raw_stream_url = None


        stream_urls = await self.aget_stream(self.bili_qn, self.bili_protocol, special_type)
        if self.unrecordable_reason:
            self.raw_stream_url = None
            return StreamProbeResult.unrecordable(self.unrecordable_reason)
        if not stream_urls:
            if self.bili_protocol == 'hls_fmp4':
                if int(time.time()) - live_start_time <= self.bili_hls_timeout:
                    logger.warning(f"{self.plugin_msg}: 暂未提供 hls_fmp4 流，等待下一次检测")
                    return StreamProbeResult.unknown("Bilibili has not provided an hls_fmp4 stream yet")
                else:
                    # 回退首个可用格式
                    stream_urls = await self.aget_stream(self.bili_qn, 'stream', special_type)
                    if self.unrecordable_reason:
                        self.raw_stream_url = None
                        return StreamProbeResult.unrecordable(self.unrecordable_reason)
            else:
                logger.error(f"{self.plugin_msg}: 获取{self.bili_protocol}流失败")
                return StreamProbeResult.unknown(f"Failed to obtain Bilibili {self.bili_protocol} stream")

        if not stream_urls:
            return StreamProbeResult.unknown("Bilibili returned no recordable stream")

        target_quality_stream = stream_urls.get(
            self.bili_qn, next(iter(stream_urls.values()))
        )
        stream_url = {}
        if self.bili_cdn is not None:
            for cdn in self.bili_cdn:
                stream_info = target_quality_stream.get(cdn)
                if stream_info is not None:
                    current_cdn = cdn
                    stream_url = stream_info['url']
                    break
        if not stream_url:
            current_cdn, stream_info = next(iter(target_quality_stream.items()))
            stream_url = stream_info['url']
            logger.debug(f"{self.plugin_msg}: 使用 {current_cdn} 流")

        self.raw_stream_url = f"{stream_url['host']}{stream_url['base_url']}{stream_url['extra']}"

        # 回退
        if self.bili_cdn_fallback:
            __url = await self.acheck_url_healthy(self.raw_stream_url)
            if __url is None:
                for cdn, stream_info in target_quality_stream.items():
                    stream_url = stream_info['url']
                    __fallback_url = f"{stream_url['host']}{stream_url['base_url']}{stream_url['extra']}"
                    try:
                        __url = await self.acheck_url_healthy(__fallback_url)
                        if __url is not None:
                            self.raw_stream_url = __url
                            logger.info(f"{self.plugin_msg}: cdn_fallback 回退到 {cdn} - {__fallback_url}")
                            break
                    except Exception as e:
                        logger.error(f"{self.plugin_msg}: cdn_fallback {e} - {__fallback_url}")
                        continue
                else:
                    logger.error(f"{self.plugin_msg}: 所有 cdn 均不可用")
                    self.raw_stream_url = None
                    return StreamProbeResult.unknown("All Bilibili CDN stream URLs are unavailable")
            else:
                self.raw_stream_url = __url

        return StreamProbeResult.live()

    def danmaku_init(self, filename_prefix=None):
        if self.bilibili_danmaku:
            self.danmaku = DanmakuClient(
                self.url, filename_prefix or self.gen_download_filename(), {
                    'room_id': self.__real_room_id,
                    'cookie': self.fake_headers.get('cookie', ''),
                    'detail': self.bilibili_danmaku_detail,
                    'raw': self.bilibili_danmaku_raw,
                    'uid': self.__login_mid
                }
            )


    async def get_play_info(self, api: str, qn: int = 10000) -> dict:
        full_url = f"{api}/xlive/web-room/v2/index/getRoomPlayInfo"
        try:
            params = {
                'room_id': str(self.__real_room_id),
                # 'no_playurl': '0',
                # 'mask': '1',
                'qn': str(qn),
                'platform': 'html5',  # 平台名称，web, html5, android, ios
                'protocol': '0,1',  # 流协议，0: http_stream(flv), 1: http_hls
                'format': '0,1,2',  # 编码格式，0: flv, 1: ts, 2: fmp4
                'codec': '0',  # 编码器，0: avc, 1: hevc, 2: av1
                # 'ptype': '8', # P2P配置，-1: disable, 8: WebRTC, 8192: MisakaTunnel
                'dolby': '5', # 杜比格式，5: 杜比音频
                # 'panorama': '1', # 全景(不支持 html5)
                # 'hdr_type': '0,1', # HDR类型(不支持 html5)，0: SDR, 1: PQ
                # 'req_reason': '0', # 请求原因，0: Normal, 1: PlayError
                # 'http': '1', # 优先 http 协议
                'web_location': WBI_WEB_LOCATION,
            }
            wbi.sign(params)
            api_res = await _bilibili_get(
                full_url, params=params, headers=self.fake_headers
            )
            api_res = json.loads(api_res.text)
            if api_res['code'] != 0:
                logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {api_res}")
                return {}
            return api_res['data']
        except json.JSONDecodeError:
            logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {api_res.text}")
        except Exception as e:
            logger.error(f"{self.plugin_msg}: {api} 获取 play_info 失败 -> {e}", exc_info=True)
        return {}

    async def get_master_m3u8(self, api: str) -> dict:
        full_url = f"{api}/xlive/play-gateway/master/url"
        params = {
            "cid": self.__real_room_id,
            "mid": self.__login_mid or self.__anchor_mid,
            "pt": "web", # platform
            "p2p_type": "-1",
            "net": 0,
            "free_type": 0,
            "build": 0,
            "feature": 2,
            "qn": self.bili_qn,
            "drm_type": 0,
            "codec": "0,1",
        }
        try:
            m3u8_res = await _bilibili_get(
                full_url, params=params, headers=self.fake_headers
            )
            if m3u8_res.status_code == 200 and m3u8_res.text.startswith("#EXTM3U"):
                return self.parse_master_m3u8(m3u8_res.text)
        except Exception as e:
            logger.error(f"{self.plugin_msg}: {api} 获取 m3u8 失败 -> {e}", exc_info=True)
        return {}

    async def aget_stream(self, qn: int = 10000, protocol: str = 'stream', special_type: int = 0) -> dict:
        """
        :param qn: 目标画质
        :param protocol: 流协议
        :param special_type: 特殊直播类型
        :return: 流信息
        """
        stream_urls = {}
        restriction_reason = None
        saw_accessible_play_info = False
        for api in self.bili_api_list:
            play_info = await self.get_play_info(api, qn)
            if not play_info:
                # logger.error(f"{self.plugin_msg}: {api} 返回内容错误: {play_info}")
                continue
            special_types = play_info.get('all_special_types') or []
            if 203 in special_types:
                restriction_reason = "Bilibili live stream is DRM protected"
                continue
            playurl_info = play_info.get('playurl_info')
            if not isinstance(playurl_info, dict):
                continue
            playurl = playurl_info.get('playurl')
            if not isinstance(playurl, dict):
                continue
            if not playurl:
                check_areablock(play_info)
                restriction_reason = "Bilibili live stream is unavailable in this region"
                continue
            streams = playurl.get('stream')
            if not isinstance(streams, list):
                continue
            if not streams:
                restriction_reason = "Bilibili returned no stream accessible to this account"
                continue
            saw_accessible_play_info = True
            if protocol == 'hls_fmp4':
                if self.bili_anonymous_origin:
                    if special_type in play_info['all_special_types'] and not self.__login_mid:
                        logger.warn(f"{self.plugin_msg}: 特殊直播{special_type}")
                    else:
                        stream_urls = await self.get_master_m3u8(api)
                        if stream_urls:
                            break
                # 处理 API 信息
                stream = streams[1] if len(streams) > 1 else streams[0]
                for format in stream['format']:
                    if format['format_name'] == 'fmp4':
                        stream_urls = self.parse_stream_url(format['codec'][0])
                        # fmp4 可能没有原画
                        if qn in {10000, 25000} and qn not in stream_urls.keys():
                            stream_urls = {}
            else:
                stream_urls = self.parse_stream_url(streams[0]['format'][0]['codec'][0])
            if stream_urls:
                self.unrecordable_reason = None
                break
        if not stream_urls and not saw_accessible_play_info and restriction_reason:
            self.unrecordable_reason = restriction_reason
        # 空字典照常返回，重试交给上层方法处理
        return stream_urls

    async def get_user_status(self) -> dict:
        try:
            nav_res = await _bilibili_get(
                'https://api.bilibili.com/x/web-interface/nav',
                headers=self.fake_headers
            )
            nav_res.raise_for_status()
            nav_res = json.loads(nav_res.text)
            if (
                nav_res['code'] == 0 or
                (nav_res['code'] == -101 and nav_res['message'] == '账号未登录')
            ):
                return nav_res['data']
            logger.error(f"{self.plugin_msg}: 获取 nav 失败-{nav_res}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(f"{self.plugin_msg}: 获取 nav 失败", exc_info=True)
        return {}

    async def update_wbi(self):
        def _extract_key(url):
            if not url:
                return None
            slash = url.rfind('/')
            dot = url.find('.', slash)
            if slash == -1 or dot == -1:
                return None
            return url[slash + 1:dot]
        data = await self.get_user_status()
        wbi_key = data.get('wbi_img')
        if wbi_key:
            img_key = _extract_key(wbi_key.get('img_url'))
            sub_key = _extract_key(wbi_key.get('sub_url'))
            if img_key and sub_key:
                wbi.update_key(img_key, sub_key)
            else:
                logger.warning(f"img_key-{img_key}, sub_key-{sub_key}")
        else:
            logger.warning(f"Can not get wbi key by {data}")

    async def check_login_status(self) -> int:
        """
        检查B站登录状态
        :return: 当前登录用户 mid
        """
        try:
            data = await self.get_user_status()
            if data.get('isLogin'):
                logger.info(f"用户名：{data['uname']}, mid: {data['mid']}")
                return data['mid']
            else:
                logger.warning(f"{self.plugin_msg}: 未登录，或将只能录制到最低画质。")
        except Exception as e:
            logger.error(f"{self.plugin_msg}: 登录态校验失败 {e}")
        return 0

    def parse_stream_url(self, *args) -> dict:
        suffix_regexp = r'suffix=([^&]+)'
        if isinstance(args[0], str):
            url = args[0]
            host = "https://" + match1(url, r'https?://([^/]+)')
            stream_url = {
                'host': host,
                'base_url': url.split("?")[0].split(host)[1] + "?",
                'extra': url.split("?")[1]
            }
            return {
                'url': stream_url,
                'stream_name': match1(url, STREAM_NAME_REGEXP),
                'suffix': match1(url, suffix_regexp)
            }
        elif isinstance(args[0], dict):
            streams = {}
            current_qn = args[0]['current_qn']
            streams.setdefault(current_qn, {})
            base_url = args[0]['base_url']
            for info in args[0]['url_info']:
                cdn_name = match1(info['extra'], r'cdn=([^&]+)')
                stream_url = {
                    'host': info['host'],
                    'base_url': base_url,
                    'extra': info['extra']
                }
                streams[current_qn].setdefault(cdn_name, {
                    'url': stream_url,
                    'stream_name': match1(base_url, STREAM_NAME_REGEXP),
                    'suffix': match1(info['extra'], suffix_regexp)
                })
            return streams


    def parse_master_m3u8(self, m3u8_content: str) -> dict:
        """
        Returns:
            {
                "qn值": {
                    "cdn名称": {
                        "url": parsed_stream_url,
                        "stream_name": "流名称",
                        "suffix": "二压后缀"
                    }
                }
            }
        """
        lines = m3u8_content.strip().splitlines()
        current_qn = None
        result = {}

        if not lines[0].startswith('#EXTM3U'):
            raise ValueError('Invalid m3u8 file')

        for line in lines:
            if line.startswith('#EXT-X-STREAM-INF:'):
                codec = match1(line, r'CODECS="([^"]+)"')
                current_qn = int(match1(line, r'BILI-QN=(\d+)'))

                if codec and current_qn:
                    if 'avc' in codec.lower():
                        result.setdefault(current_qn, {})
                    else:
                        current_qn = None

            elif line.startswith('http') and current_qn is not None:
                cdn_name = match1(line, r'cdn=([^&]+)')
                if cdn_name:
                    result[current_qn].setdefault(cdn_name, self.parse_stream_url(line))

        return dict(sorted(result.items(), key=lambda x: int(x[0]), reverse=True))

# Copy from room-player.js
def check_areablock(data):
    '''
    :return: True if area block
    '''
    if not data['playurl_info']['playurl']:
        logger.error('Sorry, bilibili is currently not available in your country according to copyright restrictions.')
        logger.error('非常抱歉，根据版权方要求，您所在的地区无法观看本直播')
        return True
    return False

def normalize_url(url: str) -> str:
    return url if url.startswith(('http://', 'https://')) else 'http://' + url
