"""Photo upload backends bundled with pibooth.

After each session, the final picture can be uploaded in the background to
a configurable photo service so the booth flow never blocks on network
access. See :py:mod:`pibooth.plugins.upload_plugin` for how backends are
wired to the booth workflow and :py:mod:`pibooth.upload.manager` for the
persisted retry queue.
"""

from typing import TYPE_CHECKING

from pibooth.upload.base import UploadBackend, UploadResult
from pibooth.utils import LOGGER

if TYPE_CHECKING:
    from pibooth.config.parser import PiConfigParser

__all__ = ["UploadBackend", "UploadResult", "create_backend"]


def create_backend(cfg: "PiConfigParser") -> "UploadBackend | None":
    """Build and configure the upload backend selected in the configuration.

    :return: a configured backend, or None (with a warning logged) if no
        service is selected or if the selected service is misconfigured
    """
    name = cfg.get("UPLOAD", "upload_backend").strip('"')

    backend: UploadBackend
    if name == "none" or not name:
        return None
    elif name == "folder":
        from pibooth.upload.folder import FolderBackend

        backend = FolderBackend()
    elif name == "webdav":
        from pibooth.upload.webdav import WebDavBackend

        backend = WebDavBackend()
    elif name == "gphotos":
        from pibooth.upload.google_photos import GooglePhotosBackend

        backend = GooglePhotosBackend()
    else:
        LOGGER.warning("Unknown upload service '%s'", name)
        return None

    try:
        backend.configure(cfg)
    except ValueError as ex:
        LOGGER.warning("Upload service '%s' is not usable: %s", name, ex)
        return None

    return backend
