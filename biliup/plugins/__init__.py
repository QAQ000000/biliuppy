"""Compatibility exports while uploader plugins move to integrations."""

from biliup.platforms import (
    SubmitOption,
    Wbi,
    generate_fake_buvid3,
    json_loads,
    logger,
    match1,
    random_user_agent,
    test_jsengine,
    wbi,
)

__all__ = [
    "SubmitOption",
    "Wbi",
    "generate_fake_buvid3",
    "json_loads",
    "logger",
    "match1",
    "random_user_agent",
    "test_jsengine",
    "wbi",
]
