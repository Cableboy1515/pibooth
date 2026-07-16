"""Upload backend for any WebDAV server (Nextcloud, ownCloud, ...).

Uses only the standard library (``urllib.request``) so no extra runtime
dependency is required for this backend.
"""

import base64
import os.path as osp
import urllib.error
import urllib.request
from typing import Any

from pibooth.upload.base import UploadBackend, UploadResult

#: Default network timeout (seconds) for all WebDAV requests
_TIMEOUT = 30


class WebDavBackend(UploadBackend):
    """Upload pictures to a WebDAV collection (e.g. Nextcloud/ownCloud)."""

    id = "webdav"
    label = "WebDAV (Nextcloud, ownCloud, ...)"

    def __init__(self) -> None:
        self.url = ""
        self._auth_header = ""

    def configure(self, cfg: Any) -> None:
        url = cfg.get("UPLOAD", "upload_webdav_url").strip('"')
        username = cfg.get("UPLOAD", "upload_webdav_user").strip('"')
        password = cfg.get("UPLOAD", "upload_webdav_password").strip('"')
        if not url:
            raise ValueError("WebDAV URL is not configured")
        if not url.endswith("/"):
            url += "/"
        self.url = url
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._auth_header = f"Basic {token}"

    def _request(
        self, method: str, url: str, data: bytes | None = None, headers: "dict[str, str] | None" = None
    ) -> Any:
        all_headers = {"Authorization": self._auth_header}
        if headers:
            all_headers.update(headers)
        request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        return urllib.request.urlopen(request, timeout=_TIMEOUT)

    def upload(self, filename: str) -> UploadResult:
        with open(filename, "rb") as fp:
            data = fp.read()
        target = self.url + osp.basename(filename)
        try:
            self._request("PUT", target, data=data)
        except urllib.error.HTTPError as ex:
            if ex.code != 404:
                raise
            # The target collection does not exist yet: create it once, then retry
            self._request("MKCOL", self.url)
            self._request("PUT", target, data=data)
        return UploadResult(url=None)

    def test(self) -> str:
        try:
            self._request("PROPFIND", self.url, headers={"Depth": "0"})
        except urllib.error.HTTPError as ex:
            raise ValueError(f"WebDAV server returned HTTP {ex.code} for '{self.url}'") from ex
        except urllib.error.URLError as ex:
            raise ValueError(f"Cannot reach WebDAV server '{self.url}': {ex.reason}") from ex
        return f"Connected to '{self.url}'"
