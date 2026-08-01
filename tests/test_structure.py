import json
import subprocess
import sys


def test_platform_package_registers_only_download_checkers() -> None:
    script = """
import json
import biliup.platforms
from biliup.engine import Plugin

Plugin.download_plugins.clear()
Plugin.upload_plugins.clear()
Plugin(biliup.platforms)
print(json.dumps({
    "downloads": len(Plugin.download_plugins),
    "uploads": len(Plugin.upload_plugins),
    "modules": [checker.__module__ for checker in Plugin.download_plugins],
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    registry = json.loads(result.stdout.strip().splitlines()[-1])

    assert registry["downloads"] == 22
    assert registry["uploads"] == 0
    assert all(module.startswith("biliup.platforms.") for module in registry["modules"])
