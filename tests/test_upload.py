import io
import json
import os
import os.path as osp
import time
import types
import urllib.error
import urllib.request

import pytest

pytest.importorskip("flask")

from pibooth.config.parser import PiConfigParser
from pibooth.plugins import create_plugin_manager
from pibooth.plugins.picture_plugin import PicturePlugin
from pibooth.upload.base import UploadResult
from pibooth.upload.folder import FolderBackend
from pibooth.upload.google_photos import GooglePhotosBackend
from pibooth.upload.manager import UploadManager
from pibooth.upload.webdav import WebDavBackend
from pibooth.web.server import create_app


@pytest.fixture
def web_cfg(tmp_path, monkeypatch):
    # Don't touch the real ~/.config/autostart entry during tests
    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


def make_client(web_cfg, application=None):
    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    app = create_app(web_cfg, pm, application)
    app.config["TESTING"] = True
    return app.test_client()


def wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("Condition not met within timeout")


# ------------------------------------------------------------- FolderBackend


def test_folder_backend_configure_creates_missing_dir(web_cfg, tmp_path):
    target = tmp_path / "usb" / "pibooth"
    web_cfg.set("UPLOAD", "upload_folder_path", str(target))
    backend = FolderBackend()
    backend.configure(web_cfg)
    assert osp.isdir(target)


def test_folder_backend_configure_missing_path_raises(web_cfg):
    backend = FolderBackend()
    with pytest.raises(ValueError):
        backend.configure(web_cfg)


def test_folder_backend_upload_copies_file(web_cfg, tmp_path):
    target = tmp_path / "usb"
    web_cfg.set("UPLOAD", "upload_folder_path", str(target))
    backend = FolderBackend()
    backend.configure(web_cfg)

    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"hello world")

    result = backend.upload(str(picture))
    assert isinstance(result, UploadResult)
    assert result.url is None
    assert (target / "pic.jpg").read_bytes() == b"hello world"


def test_folder_backend_test_writes_and_removes_marker(web_cfg, tmp_path):
    target = tmp_path / "usb"
    web_cfg.set("UPLOAD", "upload_folder_path", str(target))
    backend = FolderBackend()
    backend.configure(web_cfg)

    message = backend.test()
    assert str(target) in message
    assert not osp.isfile(osp.join(str(target), ".pibooth-test"))


# -------------------------------------------------------------- WebDavBackend


def _configure_webdav(web_cfg, url="http://example.com/dav/pibooth"):
    web_cfg.set("UPLOAD", "upload_webdav_url", url)
    web_cfg.set("UPLOAD", "upload_webdav_user", "alice")
    web_cfg.set("UPLOAD", "upload_webdav_password", "secret")
    backend = WebDavBackend()
    backend.configure(web_cfg)
    return backend


def test_webdav_configure_requires_url(web_cfg):
    backend = WebDavBackend()
    with pytest.raises(ValueError):
        backend.configure(web_cfg)


def test_webdav_upload_sends_put_with_auth_and_url(monkeypatch, web_cfg, tmp_path):
    backend = _configure_webdav(web_cfg)
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"hello")
    backend.upload(str(picture))

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "PUT"
    assert request.full_url == "http://example.com/dav/pibooth/pic.jpg"
    assert request.data == b"hello"
    assert request.get_header("Authorization") == backend._auth_header


def test_webdav_upload_retries_after_mkcol_on_404(monkeypatch, web_cfg, tmp_path):
    backend = _configure_webdav(web_cfg)
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.get_method())
        if request.get_method() == "PUT" and calls.count("PUT") == 1:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))
        return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"hello")
    backend.upload(str(picture))

    assert calls == ["PUT", "MKCOL", "PUT"]


def test_webdav_test_sends_propfind(monkeypatch, web_cfg):
    backend = _configure_webdav(web_cfg)
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    message = backend.test()
    assert len(calls) == 1
    assert calls[0].get_method() == "PROPFIND"
    assert calls[0].get_header("Depth") == "0"
    assert "example.com" in message


def test_webdav_test_reports_http_error(monkeypatch, web_cfg):
    backend = _configure_webdav(web_cfg)

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="401"):
        backend.test()


# ------------------------------------------------------------- UploadManager


class StubBackend:
    id = "stub"
    label = "Stub"

    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def upload(self, filename):
        self.calls.append(filename)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("boom")
        return UploadResult(url=f"http://example/{osp.basename(filename)}")


def test_manager_enqueue_without_backend_stays_pending(tmp_path):
    manager = UploadManager(str(tmp_path / "uploads.json"))
    manager.enqueue("pic.jpg")
    entries = manager.entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "pending"
    assert entries[0]["backend"] is None


def test_manager_worker_uploads_successfully(tmp_path):
    backend = StubBackend()
    manager = UploadManager(str(tmp_path / "uploads.json"), backend=backend, backoff_fn=lambda attempts: 0)
    manager.start()
    try:
        manager.enqueue("pic.jpg")
        wait_until(lambda: manager.entries()[0]["status"] == "done")
    finally:
        manager.stop()

    entry = manager.entries()[0]
    assert entry["status"] == "done"
    assert entry["url"] == "http://example/pic.jpg"
    assert backend.calls == ["pic.jpg"]


def test_manager_worker_retries_then_succeeds(tmp_path):
    backend = StubBackend(fail_times=2)
    manager = UploadManager(str(tmp_path / "uploads.json"), backend=backend, backoff_fn=lambda attempts: 0)
    manager.start()
    try:
        manager.enqueue("pic.jpg")
        wait_until(lambda: manager.entries()[0]["status"] == "done")
    finally:
        manager.stop()

    entry = manager.entries()[0]
    assert entry["status"] == "done"
    assert entry["attempts"] == 2
    assert backend.calls == ["pic.jpg", "pic.jpg", "pic.jpg"]


def test_manager_worker_marks_failed_after_max_attempts(tmp_path):
    backend = StubBackend(fail_times=999)
    manager = UploadManager(
        str(tmp_path / "uploads.json"), backend=backend, max_attempts=3, backoff_fn=lambda attempts: 0
    )
    manager.start()
    try:
        manager.enqueue("pic.jpg")
        wait_until(lambda: manager.entries()[0]["status"] == "failed")
    finally:
        manager.stop()

    entry = manager.entries()[0]
    assert entry["status"] == "failed"
    assert entry["attempts"] == 3
    assert entry["error"] == "boom"


def test_manager_journal_persists_across_instances(tmp_path):
    journal = str(tmp_path / "uploads.json")
    manager1 = UploadManager(journal)  # no backend configured yet: stays pending
    manager1.enqueue(str(tmp_path / "a.jpg"))

    manager2 = UploadManager(journal)
    entries = manager2.entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "pending"
    assert entries[0]["filename"] == str(tmp_path / "a.jpg")


def test_manager_retry_failed_resets_attempts(tmp_path):
    manager = UploadManager(str(tmp_path / "uploads.json"))
    manager.enqueue("x.jpg")
    entry = manager._entries[0]
    entry["status"] = "failed"
    entry["attempts"] = 5
    entry["error"] = "boom"

    manager.retry_failed()

    updated = manager.entries()[0]
    assert updated["status"] == "pending"
    assert updated["attempts"] == 0
    assert updated["error"] is None


def test_manager_trims_oldest_done_entries(tmp_path):
    manager = UploadManager(str(tmp_path / "uploads.json"))
    for i in range(205):
        manager._entries.append(
            {
                "id": str(i),
                "filename": f"pic{i}.jpg",
                "backend": None,
                "status": "done",
                "attempts": 0,
                "error": None,
                "url": None,
                "added": "",
                "updated": "",
                "next_try": 0.0,
            }
        )
    manager._trim()
    assert len(manager._entries) == 200


# --------------------------------------------------------- GooglePhotosBackend


def test_google_photos_configure_missing_token_raises(web_cfg):
    backend = GooglePhotosBackend()
    with pytest.raises(ValueError, match="pibooth-google-auth"):
        backend.configure(web_cfg)


def test_google_photos_missing_deps_raises(web_cfg):
    token_path = web_cfg.join_path("google_token.json")
    os.makedirs(osp.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fp:
        fp.write("{}")

    backend = GooglePhotosBackend()
    backend.configure(web_cfg)
    with pytest.raises(ValueError, match=r"pibooth\[gphotos\]"):
        backend._get_credentials()


# ------------------------------------------------------------------------ API


def test_uploads_api_returns_503_without_manager(web_cfg):
    client = make_client(web_cfg, application=None)
    assert client.get("/api/uploads").status_code == 503
    assert client.post("/api/uploads/retry").status_code == 503


def test_uploads_api_returns_entries_with_manager(web_cfg, tmp_path):
    manager = UploadManager(str(tmp_path / "uploads.json"))
    manager.enqueue(str(tmp_path / "pic.jpg"))
    application = types.SimpleNamespace(upload_manager=manager)
    client = make_client(web_cfg, application=application)

    response = client.get("/api/uploads")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["backend"] == "none"
    assert len(payload["entries"]) == 1

    assert client.post("/api/uploads/retry").status_code == 200


def test_uploads_api_test_endpoint_works_without_manager(web_cfg, tmp_path):
    target = tmp_path / "usb"
    web_cfg.set("UPLOAD", "upload_backend", "folder")
    web_cfg.set("UPLOAD", "upload_folder_path", str(target))
    client = make_client(web_cfg, application=None)

    response = client.post("/api/uploads/test")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_uploads_api_test_endpoint_rejects_unconfigured(web_cfg):
    client = make_client(web_cfg)
    assert client.post("/api/uploads/test").status_code == 400


def test_uploads_api_google_credentials_client_secret(web_cfg):
    client = make_client(web_cfg)
    payload = json.dumps({"installed": {"client_id": "x"}}).encode("utf-8")

    response = client.post(
        "/api/uploads/google/credentials",
        data={"file": (io.BytesIO(payload), "secret.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["kind"] == "client secret"
    assert osp.isfile(web_cfg.join_path("google_client_secret.json"))


def test_uploads_api_google_credentials_token(web_cfg):
    client = make_client(web_cfg)
    payload = json.dumps({"refresh_token": "r", "client_id": "c"}).encode("utf-8")

    response = client.post(
        "/api/uploads/google/credentials",
        data={"file": (io.BytesIO(payload), "token.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["kind"] == "token"
    assert osp.isfile(web_cfg.join_path("google_token.json"))


def test_uploads_api_google_credentials_rejects_garbage(web_cfg):
    client = make_client(web_cfg)

    response = client.post(
        "/api/uploads/google/credentials",
        data={"file": (io.BytesIO(b"not json"), "x.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
