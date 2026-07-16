import io
import os.path as osp

import pytest
from PIL import Image

pytest.importorskip("flask")

from pibooth.config.parser import PiConfigParser
from pibooth.plugins import create_plugin_manager
from pibooth.plugins.picture_plugin import PicturePlugin
from pibooth.web.server import create_app


@pytest.fixture
def web_cfg(tmp_path, monkeypatch):
    # Don't touch the real ~/.config/autostart entry during tests
    monkeypatch.setattr(PiConfigParser, "handle_autostart", lambda self: None)
    cfg = PiConfigParser(str(tmp_path / "pibooth.cfg"), None)
    cfg.set("GENERAL", "directory", str(tmp_path / "pictures"))
    return cfg


@pytest.fixture
def client(web_cfg):
    pm = create_plugin_manager()
    pm.register(PicturePlugin(pm), name="pibooth-core:picture")
    app = create_app(web_cfg, pm, None)
    app.config["TESTING"] = True
    return app.test_client()


def test_get_config_schema(client):
    payload = client.get("/api/config").get_json()
    sections = {section["name"]: section for section in payload["sections"]}
    assert "GENERAL" in sections and "PICTURE" in sections and "WEB" in sections

    camera_options = {opt["name"]: opt for opt in sections["CAMERA"]["options"]}
    assert camera_options["type"]["kind"] == "choice"
    assert "auto" in camera_options["type"]["choices"]

    picture_options = {opt["name"]: opt for opt in sections["PICTURE"]["options"]}
    assert picture_options["captures"]["kind"] == "captures"
    assert picture_options["overlays"]["kind"] == "image"
    assert picture_options["backgrounds"]["kind"] == "color_or_image"
    assert picture_options["footer_text1"]["quoted"] is True

    window_options = {opt["name"]: opt for opt in sections["WINDOW"]["options"]}
    assert window_options["text_color"]["kind"] == "color"
    assert window_options["flash"]["kind"] == "bool"


def test_put_config_saves_values(client, web_cfg):
    response = client.put("/api/config", json={"values": {"PICTURE": {"footer_text1": '"My Event"'}}})
    assert response.status_code == 200
    assert web_cfg.get("PICTURE", "footer_text1") == '"My Event"'
    assert osp.isfile(web_cfg.filename)
    with open(web_cfg.filename) as fp:
        assert 'footer_text1 = "My Event"' in fp.read()


def test_put_config_rejects_unknown(client):
    assert client.put("/api/config", json={"values": {"NOPE": {"foo": "1"}}}).status_code == 400
    assert client.put("/api/config", json={"values": {"PICTURE": {"nope": "1"}}}).status_code == 400
    assert client.put("/api/config", json={}).status_code == 400


def test_assets_upload_list_serve_delete(client, web_cfg):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(buffer, format="PNG")
    buffer.seek(0)

    response = client.post(
        "/api/assets",
        data={"file": (buffer, "my background.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    asset = response.get_json()
    assert asset["name"] == "my_background.png"
    assert asset["path"] == osp.join(osp.dirname(web_cfg.filename), "assets", "my_background.png")
    assert osp.isfile(asset["path"])

    names = [item["name"] for item in client.get("/api/assets").get_json()["assets"]]
    assert "my_background.png" in names

    assert client.get("/api/assets/my_background.png").status_code == 200
    assert client.delete("/api/assets/my_background.png").status_code == 200
    assert client.get("/api/assets/my_background.png").status_code == 404


def test_assets_upload_rejects_non_image(client):
    response = client.post(
        "/api/assets",
        data={"file": (io.BytesIO(b"boom"), "evil.sh")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_preview_returns_png(client):
    response = client.get("/api/preview?variant=0")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")

    # Second variant (default captures choices are 4 and 1)
    response = client.get("/api/preview?variant=1")
    assert response.status_code == 200


def test_status(client):
    payload = client.get("/api/status").get_json()
    assert payload["camera"]["configured"] == "auto"
    assert payload["printer"]["connected"] is False


def test_printers_endpoint(client):
    payload = client.get("/api/printers").get_json()
    assert "available" in payload
    assert isinstance(payload["printers"], list)


def test_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"pibooth" in response.data
