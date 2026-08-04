from .decorators import Plugin
from .status import StreamProbeResult, StreamStatus


def invert_dict(d: dict):
    inverse_dict = {}
    for k, v in d.items():
        if isinstance(v, list):
            for item in v:
                inverse_dict[item] = k
        else:
            inverse_dict[v] = k
    return inverse_dict


__all__ = ["invert_dict", "Plugin", "StreamProbeResult", "StreamStatus"]
