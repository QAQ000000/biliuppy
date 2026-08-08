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


def test_integration_package_registers_only_uploaders() -> None:
    script = """
import json
import biliup.integrations.uploaders
from biliup.engine import Plugin

Plugin.download_plugins.clear()
Plugin.upload_plugins.clear()
Plugin(biliup.integrations.uploaders)
print(json.dumps({
    "downloads": len(Plugin.download_plugins),
    "uploads": len(Plugin.upload_plugins),
    "modules": [uploader.__module__ for uploader in Plugin.upload_plugins.values()],
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    registry = json.loads(result.stdout.strip().splitlines()[-1])

    assert registry["downloads"] == 0
    assert registry["uploads"] == 5
    assert all(module.startswith("biliup.integrations.uploaders.") for module in registry["modules"])
