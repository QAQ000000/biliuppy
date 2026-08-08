from __future__ import annotations

import time
from typing import Any


def render_upload_text(template: str, context: dict[str, Any]) -> str:
    try:
        formatted = template.format(
            streamer=context["name"],
            title=context["room_title"],
            url=context["url"],
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid upload template: {template}") from exc
    return time.strftime(formatted, time.localtime(context["start_time"]))
