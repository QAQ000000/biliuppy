from __future__ import annotations

import re

SENSITIVE_VALUE = re.compile(
    r"(?i)(?P<prefix>['\"]?(?:csrf(?:_token)?|access_key|access_token|refresh_token|"
    r"token|uptoken|w_rid|signature|upload_id|b_wet|x-upos-auth|"
    r"x-amz-(?:credential|signature|security-token))['\"]?\s*[=:]\s*['\"]?)"
    r"(?P<value>[^&\s,'\"}\]]+)"
)
SENSITIVE_HEADER = re.compile(
    r"(?i)(?P<prefix>\b(?:cookie|authorization)\s*[=:]\s*)(?P<value>[^\s]+)"
)


def redact_sensitive_text(message: str) -> str:
    message = SENSITIVE_VALUE.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    return SENSITIVE_HEADER.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
