from __future__ import annotations

import asyncio
from json import JSONDecodeError

import aiohttp
import requests


class UploadRejectedError(RuntimeError):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.code = payload.get("code")
        message = payload.get("message") or payload.get("msg") or str(payload)
        super().__init__(f"Bilibili rejected the submission: code={self.code} message={message}")


class UploadCancelledError(RuntimeError):
    pass


class UploadOutcomeUnknownError(RuntimeError):
    """Submission may have reached Bilibili, so automatic retry is unsafe."""


class UploadSubmissionRetryExhaustedError(RuntimeError):
    """Uploaded parts are complete, but submission retries were exhausted."""


class TransientUploadError(RuntimeError):
    """The upload failed before submission and can be retried safely."""


def is_transient_upload_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (UploadOutcomeUnknownError, UploadSubmissionRetryExhaustedError)):
            return False
        if isinstance(
            current,
            (
                TransientUploadError,
                asyncio.TimeoutError,
                aiohttp.ClientError,
                requests.Timeout,
                requests.ConnectionError,
            ),
        ):
            return True
        if isinstance(current, requests.HTTPError):
            status = current.response.status_code if current.response is not None else None
            return status == 429 or bool(status and status >= 500)
        if isinstance(current, JSONDecodeError):
            return True
        if isinstance(current, UploadRejectedError):
            if current.code == 21615:
                return True
            message = str(current).lower()
            return any(marker in message for marker in ("网络繁忙", "服务繁忙", "稍后再试", "temporarily unavailable"))
        current = current.__cause__ or current.__context__
    return False
