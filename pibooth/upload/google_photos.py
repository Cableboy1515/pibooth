"""Google Photos upload backend.

Google restricted the Photos Library API in 2025: third-party apps can only
create albums and append media to albums *they created themselves* (scope
``https://www.googleapis.com/auth/photoslibrary.appendonly``); listing the
user's existing albums or media is no longer available. As a consequence,
pictures uploaded by pibooth always land in an album created by this
backend (name configurable, "Pibooth" by default), and no shareable album
URL can be retrieved without requesting a broader, more sensitive scope.

Authorization is a two-step, one-time process:

1. Create an OAuth client of type "Desktop app" in the Google Cloud console
   (with the Photos Library API enabled) and download its client secret
   JSON.
2. Run :command:`pibooth-google-auth <client_secret.json>` on any computer
   with a web browser (not necessarily the booth itself, see
   :py:mod:`pibooth.scripts.google_auth`). It writes a ``google_token.json``
   next to the client secret file.

Both JSON files are then copied (or uploaded through the web interface
"Uploads" page) into the booth configuration directory as
``google_client_secret.json`` and ``google_token.json``.
"""

import json
import os.path as osp
import urllib.error
import urllib.request
from typing import Any

from pibooth.upload.base import UploadBackend, UploadResult

#: Scope allowing upload + creation of app-owned albums only
SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]

_API_BASE = "https://photoslibrary.googleapis.com/v1"


class GooglePhotosBackend(UploadBackend):
    """Upload pictures to an app-created Google Photos album."""

    id = "gphotos"
    label = "Google Photos"

    def __init__(self) -> None:
        self.token_path = ""
        self.album_name = "Pibooth"
        self.album_map_path = ""
        self._credentials: Any = None

    def configure(self, cfg: Any) -> None:
        self.token_path = cfg.join_path("google_token.json")
        self.album_map_path = cfg.join_path("google_album.json")
        self.album_name = cfg.get("UPLOAD", "upload_album").strip('"') or "Pibooth"

        if not osp.isfile(self.token_path):
            raise ValueError(
                "Google Photos is not authorized yet: run 'pibooth-google-auth' on a computer with a "
                "browser, then upload the generated 'google_client_secret.json' and 'google_token.json' "
                "files from the Uploads settings page"
            )

    def _get_credentials(self) -> Any:
        """Load (and refresh if needed) the authorized user credentials."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as ex:
            raise ValueError(
                "Google Photos support requires extra packages, install with 'pip install pibooth[gphotos]'"
            ) from ex

        if self._credentials is None:
            self._credentials = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not self._credentials.valid:
            if self._credentials.expired and self._credentials.refresh_token:
                self._credentials.refresh(Request())
                with open(self.token_path, "w", encoding="utf-8") as fp:
                    fp.write(self._credentials.to_json())
            else:
                raise ValueError(
                    "Google Photos authorization has expired, run 'pibooth-google-auth' again and "
                    "re-upload 'google_token.json'"
                )
        return self._credentials

    def _request(
        self, method: str, url: str, data: bytes | None = None, headers: "dict[str, str] | None" = None
    ) -> bytes:
        credentials = self._get_credentials()
        all_headers = {"Authorization": f"Bearer {credentials.token}"}
        if headers:
            all_headers.update(headers)
        request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        with urllib.request.urlopen(request, timeout=60) as response:
            return bytes(response.read())

    def _load_album_map(self) -> "dict[str, str]":
        if osp.isfile(self.album_map_path):
            try:
                with open(self.album_map_path, encoding="utf-8") as fp:
                    return dict(json.load(fp))
            except (OSError, ValueError):
                return {}
        return {}

    def _save_album_map(self, album_map: "dict[str, str]") -> None:
        with open(self.album_map_path, "w", encoding="utf-8") as fp:
            json.dump(album_map, fp)

    def _get_or_create_album_id(self) -> str:
        album_map = self._load_album_map()
        album_id = album_map.get(self.album_name)
        if album_id:
            return album_id

        payload = json.dumps({"album": {"title": self.album_name}}).encode("utf-8")
        response = self._request(
            "POST", f"{_API_BASE}/albums", data=payload, headers={"Content-Type": "application/json"}
        )
        album_id = str(json.loads(response)["id"])
        album_map[self.album_name] = album_id
        self._save_album_map(album_map)
        return album_id

    def upload(self, filename: str) -> UploadResult:
        album_id = self._get_or_create_album_id()

        with open(filename, "rb") as fp:
            file_bytes = fp.read()

        upload_token = self._request(
            "POST",
            f"{_API_BASE}/uploads",
            data=file_bytes,
            headers={
                "Content-type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": "image/jpeg",
                "X-Goog-Upload-Protocol": "raw",
            },
        ).decode("utf-8")

        payload = json.dumps(
            {
                "albumId": album_id,
                "newMediaItems": [
                    {"simpleMediaItem": {"fileName": osp.basename(filename), "uploadToken": upload_token}}
                ],
            }
        ).encode("utf-8")
        self._request(
            "POST", f"{_API_BASE}/mediaItems:batchCreate", data=payload, headers={"Content-Type": "application/json"}
        )
        return UploadResult(url=None)

    def test(self) -> str:
        try:
            self._get_or_create_album_id()
        except urllib.error.HTTPError as ex:
            raise ValueError(
                f"Google Photos API returned HTTP {ex.code}: {ex.read().decode('utf-8', 'replace')}"
            ) from ex
        except urllib.error.URLError as ex:
            raise ValueError(f"Cannot reach Google Photos API: {ex.reason}") from ex
        return f"Authenticated — album '{self.album_name}' ready"
