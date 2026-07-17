"""Static syntax check for the web interface's frontend (no JS test runner
is set up for this project, so this is our CI safety net against typos).
"""

import os
import os.path as osp
import shutil
import subprocess

import pytest

HERE = osp.dirname(osp.abspath(__file__))
STATIC_DIR = osp.join(HERE, "..", "pibooth", "web", "static")

NODE = shutil.which("node")


def _static_js_files() -> list[str]:
    if not osp.isdir(STATIC_DIR):
        return []
    return sorted(name for name in os.listdir(STATIC_DIR) if name.endswith(".js"))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("filename", _static_js_files())
def test_static_js_syntax(filename: str) -> None:
    path = osp.join(STATIC_DIR, filename)
    result = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
