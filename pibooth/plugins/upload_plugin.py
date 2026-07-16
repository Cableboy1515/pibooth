"""Plugin uploading the final picture (and optionally the raw captures) to
a configurable photo service after each session, entirely in the
background so the booth flow never blocks on network access.
"""

import glob
import os.path as osp
from typing import TYPE_CHECKING, Any

import pibooth
from pibooth.upload import create_backend
from pibooth.upload.manager import UploadManager
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.booth import PiApplication
    from pibooth.config.parser import PiConfigParser

#: UPLOAD options that require rebuilding the backend when they change
_WATCHED_OPTIONS = (
    "upload_backend",
    "upload_album",
    "upload_folder_path",
    "upload_webdav_url",
    "upload_webdav_user",
    "upload_webdav_password",
)


class UploadPlugin:
    """Plugin managing the background photo upload queue."""

    name = "pibooth-core:upload"

    def __init__(self, plugin_manager: Any) -> None:
        self._pm = plugin_manager
        self.manager: UploadManager | None = None
        self._settings: tuple[str, ...] | None = None

    def _current_settings(self, cfg: "PiConfigParser") -> tuple[str, ...]:
        return tuple(cfg.get("UPLOAD", name) for name in _WATCHED_OPTIONS)

    def _rebuild_backend(self, cfg: "PiConfigParser") -> None:
        assert self.manager is not None
        self.manager.set_backend(create_backend(cfg))
        self._settings = self._current_settings(cfg)

    @pibooth.hookimpl
    def pibooth_startup(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        self.manager = UploadManager(cfg.join_path("uploads.json"))
        self._rebuild_backend(cfg)
        self.manager.start()
        LOGGER.info("Upload manager started")
        # Expose the manager to the web interface (see pibooth.web.uploads_api).
        # PiApplication does not declare this attribute upfront: the web API
        # falls back gracefully (getattr with default) when it is missing.
        app.upload_manager = self.manager  # type: ignore[attr-defined]

    @pibooth.hookimpl
    def state_wait_enter(self, cfg: "PiConfigParser") -> None:
        # Cheap settings-changed check, done on every state transition so
        # that configuration changes applied from the web UI take effect
        # without requiring a restart.
        if self.manager is not None and self._settings != self._current_settings(cfg):
            self._rebuild_backend(cfg)

    @pibooth.hookimpl
    def state_processing_exit(self, cfg: "PiConfigParser", app: "PiApplication") -> None:
        if self.manager is None:
            return

        if app.previous_picture_file:
            self.manager.enqueue(app.previous_picture_file)

        if cfg.getboolean("UPLOAD", "upload_originals") and app.capture_date:
            for savedir in cfg.gettuple("GENERAL", "directory", "path"):
                rawdir = osp.join(savedir, "raw", app.capture_date)
                for capture in sorted(glob.glob(osp.join(rawdir, "pibooth*.jpg"))):
                    self.manager.enqueue(capture)

    @pibooth.hookimpl
    def pibooth_cleanup(self, app: "PiApplication") -> None:
        if self.manager is not None:
            self.manager.stop()
            self.manager = None
