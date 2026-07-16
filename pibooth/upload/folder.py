"""Upload backend copying the picture into a local/mounted directory
(e.g. a USB stick or a NAS/Syncthing share mounted on the booth).
"""

import os
import os.path as osp
import shutil
from typing import Any

from pibooth.upload.base import UploadBackend, UploadResult


class FolderBackend(UploadBackend):
    """Copy pictures into a target directory."""

    id = "folder"
    label = "Folder (USB stick / NAS share)"

    def __init__(self) -> None:
        self.path = ""

    def configure(self, cfg: Any) -> None:
        path = cfg.get("UPLOAD", "upload_folder_path").strip('"')
        if not path:
            raise ValueError("Upload folder path is not configured")
        path = osp.abspath(osp.expanduser(path))
        if not osp.isdir(path):
            try:
                os.makedirs(path)
            except OSError as ex:
                raise ValueError(f"Cannot create upload folder '{path}': {ex}") from ex
        self.path = path

    def upload(self, filename: str) -> UploadResult:
        shutil.copy2(filename, osp.join(self.path, osp.basename(filename)))
        return UploadResult(url=None)

    def test(self) -> str:
        marker = osp.join(self.path, ".pibooth-test")
        with open(marker, "w", encoding="utf-8") as fp:
            fp.write("pibooth upload test\n")
        os.remove(marker)
        return f"Folder '{self.path}' is writable"
